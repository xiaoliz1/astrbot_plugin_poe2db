"""Persistent storage for generated price-table images."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path


_MARKETS = frozenset({"international", "national"})
_IMAGE_NAME = re.compile(
    r"^(?:international|national)-[0-9a-f]{32}\.(?:png|jpg|webp)$"
)
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"RIFF", ".webp"),
)


class PriceImageStore:
    """Keep each market's latest image until a replacement is ready."""

    def __init__(self, data_dir: str | Path) -> None:
        self.directory = Path(data_dir) / "price_images"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.directory / "latest.json"
        self.images: dict[str, str] = {}
        self.updated_at: dict[str, str] = {}
        self._load_manifest()

    def save_replacement(
        self,
        market: str,
        rendered_path: str | Path,
        updated_at: str,
    ) -> str:
        """Persist a complete rendered image, switch the manifest, then prune old files."""
        if market not in _MARKETS:
            raise ValueError(f"Unsupported price market: {market}")

        source = Path(rendered_path)
        if not source.is_file() or source.stat().st_size == 0:
            raise ValueError("Rendered price image is missing or empty")

        image_id = uuid.uuid4().hex
        suffix = self._image_suffix(source)
        filename = f"{market}-{image_id}{suffix}"
        destination = self.directory / filename
        temporary_image = self.directory / f".{market}-{image_id}.tmp"

        try:
            shutil.copyfile(source, temporary_image)
            if temporary_image.stat().st_size == 0:
                raise ValueError("Persisted price image is empty")
            os.replace(temporary_image, destination)
        except Exception:
            temporary_image.unlink(missing_ok=True)
            raise

        next_images = dict(self.images)
        next_updated_at = dict(self.updated_at)
        next_images[market] = str(destination.resolve())
        next_updated_at[market] = updated_at
        try:
            self._write_manifest(next_images, next_updated_at)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

        self.images.clear()
        self.images.update(next_images)
        self.updated_at.clear()
        self.updated_at.update(next_updated_at)
        self._remove_obsolete_images()
        return self.images[market]

    def _load_manifest(self) -> None:
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError):
            return

        entries = manifest.get("images") if isinstance(manifest, dict) else None
        if not isinstance(entries, dict):
            return

        for market in _MARKETS:
            entry = entries.get(market)
            if not isinstance(entry, dict):
                continue
            filename = entry.get("filename")
            updated_at = entry.get("updated_at")
            if not isinstance(filename, str) or not _IMAGE_NAME.fullmatch(filename):
                continue
            path = self.directory / filename
            if not path.is_file():
                continue
            self.images[market] = str(path.resolve())
            if isinstance(updated_at, str):
                self.updated_at[market] = updated_at

    def _write_manifest(
        self,
        images: dict[str, str],
        updated_at: dict[str, str],
    ) -> None:
        manifest = {
            "version": 1,
            "images": {
                market: {
                    "filename": Path(path).name,
                    "updated_at": updated_at.get(market, ""),
                }
                for market, path in images.items()
                if market in _MARKETS
            },
        }
        temporary_manifest = self.manifest_path.with_name(
            f".{self.manifest_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary_manifest.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary_manifest, self.manifest_path)
        finally:
            temporary_manifest.unlink(missing_ok=True)

    @staticmethod
    def _image_suffix(source: Path) -> str:
        with source.open("rb") as image_file:
            header = image_file.read(12)
        for signature, suffix in _IMAGE_SIGNATURES:
            if header.startswith(signature):
                if suffix != ".webp" or header[8:12] == b"WEBP":
                    return suffix
        return ".jpg" if source.suffix.lower() in {".jpg", ".jpeg"} else ".png"

    def _remove_obsolete_images(self) -> None:
        active_names = {Path(path).name for path in self.images.values()}
        for path in self.directory.iterdir():
            if (
                path.is_file()
                and _IMAGE_NAME.fullmatch(path.name)
                and path.name not in active_names
            ):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    # A failed cleanup must not invalidate the newly saved image.
                    continue
