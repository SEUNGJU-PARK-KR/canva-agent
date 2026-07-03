from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx


class CanvaAPIError(RuntimeError):
    pass


@dataclass
class CanvaConnectClient:
    access_token: str
    base_url: str = "https://api.canva.com/rest/v1"
    timeout: float = 30.0

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        with httpx.Client(timeout=self.timeout) as client:
            response = client.request(method, url, headers=self._headers(), json=payload)
        if response.status_code >= 400:
            raise CanvaAPIError(
                f"Canva API error {response.status_code}: {response.text[:1000]}"
            )
        if not response.content:
            return {}
        return response.json()

    def create_url_asset_upload_job(
        self,
        name: str,
        url: str,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": name,
            "url": url,
        }
        if tags:
            payload["tags"] = tags
        return self._request("POST", "/url-asset-uploads", payload)

    def get_url_asset_upload_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/url-asset-uploads/{job_id}")

    def create_export_job(
        self,
        design_id: str,
        export_format: str = "pdf",
        pages: list[int] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "design_id": design_id,
            "format": {"type": export_format},
        }
        if pages:
            payload["format"]["pages"] = pages
        return self._request("POST", "/exports", payload)

    def get_export_job(self, export_id: str) -> dict[str, Any]:
        return self._request("GET", f"/exports/{export_id}")

    def create_autofill_job_from_brand_template(
        self,
        brand_template_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "brand_template_id": brand_template_id,
            "data": data,
        }
        return self._request("POST", "/autofills", payload)


def wait_for_job(
    getter: Callable[[str], dict[str, Any]],
    job_id: str,
    timeout_sec: int = 120,
    interval_sec: float = 2.0,
) -> dict[str, Any]:
    deadline = time.time() + timeout_sec
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = getter(job_id)
        status = _find_status(last)
        if status in {"success", "failed"}:
            return last
        time.sleep(interval_sec)
    raise TimeoutError(f"작업이 {timeout_sec}초 안에 끝나지 않았습니다: {job_id}")


def _find_status(payload: dict[str, Any]) -> str | None:
    if "status" in payload:
        return str(payload["status"])
    job = payload.get("job")
    if isinstance(job, dict) and "status" in job:
        return str(job["status"])
    return None
