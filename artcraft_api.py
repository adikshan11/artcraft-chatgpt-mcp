import getpass
import json
import os
import re
import subprocess
import sys
import uuid
from urllib import error, parse, request


API_BASE_URL = "https://api.storyteller.ai"
KEYCHAIN_SERVICE = "artcraft-chatgpt-mcp"
MAX_IMAGE_BATCH_COUNT = 4
JOB_TOKEN_PATTERN = re.compile(r"jinf_[A-Za-z0-9_-]{1,100}\Z")
MODEL_PATTERN = re.compile(r"[a-z0-9_]{1,80}\Z")
PENDING_JOB_STATES = {"pending", "started", "attempt_failed"}
FAILED_JOB_STATES = {
    "complete_failure",
    "dead",
    "cancelled_by_user",
    "cancelled_by_system",
}


class ArtCraftAPIError(RuntimeError):
    """The ArtCraft API request failed or returned an invalid response."""


class _NoRedirectHandler(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise error.HTTPError(req.full_url, code, msg, headers, fp)


def get_api_key() -> str:
    if api_key := os.environ.get("ARTCRAFT_API_KEY", "").strip():
        return api_key

    if sys.platform != "darwin":
        raise ArtCraftAPIError(
            "No ArtCraft API key is configured. Set ARTCRAFT_API_KEY or run this "
            "server on macOS with the key saved in Keychain."
        )

    try:
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                getpass.getuser(),
                "-s",
                KEYCHAIN_SERVICE,
                "-w",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError as exc:
        raise ArtCraftAPIError("The macOS Keychain utility is unavailable.") from exc
    except subprocess.CalledProcessError as exc:
        raise ArtCraftAPIError(
            "No ArtCraft API key was found in your macOS Keychain. "
            "Save it as a password item before generating images."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise ArtCraftAPIError("Reading the ArtCraft key from Keychain timed out.") from exc

    api_key = result.stdout.strip()
    if not api_key:
        raise ArtCraftAPIError("The ArtCraft API key in Keychain is empty.")
    return api_key


def _http_error_message(exc: error.HTTPError) -> str:
    if exc.code == 401:
        return "ArtCraft rejected the API key. Check that it is valid and enabled."
    if exc.code == 402:
        return "ArtCraft reported insufficient credits or balance; no image was returned."
    if exc.code == 403:
        return "ArtCraft API access is not enabled for this account or key."
    if exc.code == 429:
        return "ArtCraft rate-limited this request. Wait before trying again."
    if exc.code == 400:
        try:
            body = json.loads(exc.read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = {}
        if isinstance(body, dict):
            detail = body.get("BadInput") or body.get("message")
            if isinstance(detail, str) and detail.strip():
                return f"ArtCraft rejected the request: {detail[:300]}"
        return "ArtCraft rejected the request. Check the model and generation options."
    if 300 <= exc.code < 400:
        return "ArtCraft returned an unexpected redirect; the request was not followed."
    return f"ArtCraft API request failed with HTTP {exc.code}."


def _request_json(path: str, method: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {get_api_key()}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(
        f"{API_BASE_URL}{path}", data=data, headers=headers, method=method
    )
    try:
        opener = request.build_opener(_NoRedirectHandler())
        with opener.open(req, timeout=30) as response:
            result = json.loads(response.read())
    except error.HTTPError as exc:
        raise ArtCraftAPIError(_http_error_message(exc)) from exc
    except (error.URLError, TimeoutError) as exc:
        raise ArtCraftAPIError("Could not reach the ArtCraft API.") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ArtCraftAPIError("ArtCraft returned an invalid JSON response.") from exc

    if not isinstance(result, dict):
        raise ArtCraftAPIError("ArtCraft returned an unexpected response.")
    return result


def submit_image_generation(prompt: str, model: str, image_batch_count: int = 1) -> dict:
    prompt = prompt.strip()
    model = model.strip()
    if not prompt:
        raise ArtCraftAPIError("Provide a non-empty image prompt.")
    if not MODEL_PATTERN.fullmatch(model):
        raise ArtCraftAPIError("Provide a valid ArtCraft image model identifier.")
    if not 1 <= image_batch_count <= MAX_IMAGE_BATCH_COUNT:
        raise ArtCraftAPIError(
            f"image_batch_count must be between 1 and {MAX_IMAGE_BATCH_COUNT}."
        )

    result = _request_json(
        "/v1/omni_api/generate/image",
        "POST",
        {
            "idempotency_token": str(uuid.uuid4()),
            "model": model,
            "prompt": prompt,
            "image_batch_count": image_batch_count,
        },
    )
    job_token = result.get("inference_job_token")
    if result.get("success") is not True or not isinstance(job_token, str):
        raise ArtCraftAPIError("ArtCraft did not confirm that the image job was accepted.")
    if not JOB_TOKEN_PATTERN.fullmatch(job_token):
        raise ArtCraftAPIError("ArtCraft returned an invalid image job token.")

    return {
        "job_token": job_token,
        "model": model,
        "status": "pending",
        "message": (
            "ArtCraft accepted the image request. Use get_artcraft_image_job with this "
            "job token to check progress and retrieve the image URL."
        ),
    }


def get_image_job(job_token: str) -> dict:
    if not JOB_TOKEN_PATTERN.fullmatch(job_token):
        raise ArtCraftAPIError("Provide a valid ArtCraft image job token beginning with jinf_.")

    result = _request_json(
        f"/v1/omni_api/job_status/job/{parse.quote(job_token, safe='')}",
        "GET",
    )
    state = result.get("state")
    if result.get("success") is not True or not isinstance(state, dict):
        raise ArtCraftAPIError("ArtCraft did not return a valid image job status.")

    status_data = state.get("status")
    if not isinstance(status_data, dict) or not isinstance(status_data.get("status"), str):
        raise ArtCraftAPIError("ArtCraft returned an unexpected image job status.")
    status = status_data["status"]
    progress = status_data.get("progress_percentage")
    if progress is not None and (
        type(progress) is not int or not 0 <= progress <= 100
    ):
        raise ArtCraftAPIError("ArtCraft returned an invalid job progress percentage.")
    failure_category = status_data.get("maybe_failure_category")
    if failure_category is not None and not isinstance(failure_category, str):
        raise ArtCraftAPIError("ArtCraft returned an invalid job failure category.")

    response = {
        "job_token": job_token,
        "status": status,
        "progress_percentage": progress,
        "image_url": None,
        "failure_category": failure_category,
        "message": None,
    }
    if status == "complete_success":
        media_result = state.get("maybe_result")
        media_links = (
            media_result.get("media_links")
            if isinstance(media_result, dict)
            else None
        )
        image_url = media_links.get("cdn_url") if isinstance(media_links, dict) else None
        if not isinstance(image_url, str) or not image_url.startswith("https://"):
            raise ArtCraftAPIError(
                "ArtCraft marked the job complete but returned no valid HTTPS image URL."
            )
        response["image_url"] = image_url
        response["message"] = "Image generation completed."
    elif status in FAILED_JOB_STATES:
        response["message"] = f"Image generation ended without an image ({status})."
    elif status in PENDING_JOB_STATES:
        response["message"] = "Image generation is still in progress. Check this job again shortly."
    else:
        raise ArtCraftAPIError(f"ArtCraft returned an unrecognized image job state: {status}.")

    return response
