from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter

try:
    from simple_lama_inpainting import SimpleLama
except ImportError:  # pragma: no cover - handled at runtime for missing deps
    SimpleLama = None

try:
    from rapidocr import RapidOCR
except ImportError:  # pragma: no cover - handled at runtime for missing deps
    RapidOCR = None


@dataclass(frozen=True)
class OCRDetection:
    text: str
    score: float
    polygon: tuple[tuple[float, float], ...]
    matched: bool


def _odd_size(value: int) -> int:
    value = max(1, int(value))
    return value if value % 2 == 1 else value + 1


def parse_rois(roi_items: Iterable[str], width: int, height: int) -> list[tuple[int, int, int, int]]:
    rois: list[tuple[int, int, int, int]] = []
    for item in roi_items:
        for chunk in item.split(";"):
            text = chunk.strip()
            if not text:
                continue

            parts = [part.strip() for part in text.split(",")]
            if len(parts) != 4:
                raise ValueError(f"ROI 格式必须是 x,y,w,h，收到: {text}")

            try:
                x, y, w, h = [int(part) for part in parts]
            except ValueError as exc:
                raise ValueError(f"ROI 必须是整数: {text}") from exc

            if w <= 0 or h <= 0:
                raise ValueError(f"ROI 的宽高必须大于 0: {text}")

            left = max(0, x)
            top = max(0, y)
            right = min(width, left + w)
            bottom = min(height, top + h)
            if right <= left or bottom <= top:
                raise ValueError(f"ROI 超出图片范围: {text}")

            rois.append((left, top, right, bottom))

    return rois


def _normalize_mask(mask: Image.Image, feather: int, threshold: int) -> Image.Image:
    grayscale = mask.convert("L")
    if feather > 0:
        grayscale = grayscale.filter(ImageFilter.GaussianBlur(radius=feather))
    return grayscale.point(lambda value: 255 if value >= threshold else 0).convert("L")


def _expand_mask(mask: Image.Image, expand: int) -> Image.Image:
    if expand <= 0:
        return mask
    return mask.filter(ImageFilter.MaxFilter(size=_odd_size(expand * 2 + 1)))


def _scale_mask(mask: Image.Image, factor: float) -> Image.Image:
    factor = max(0.0, min(1.0, float(factor)))
    if factor >= 1.0:
        return mask.convert("L")
    return mask.convert("L").point(lambda value: int(round(value * factor)))


def build_mask_from_rois(
    size: tuple[int, int],
    rois: Iterable[tuple[int, int, int, int]],
    expand: int,
    feather: int,
    threshold: int,
) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for left, top, right, bottom in rois:
        draw.rectangle((left, top, right, bottom), fill=255)

    mask = _expand_mask(mask, expand)
    return _normalize_mask(mask, feather=feather, threshold=threshold)


def build_mask_from_polygons(
    size: tuple[int, int],
    polygons: Iterable[Iterable[tuple[float, float]]],
    expand: int,
    feather: int,
    threshold: int,
) -> Image.Image:
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for polygon in polygons:
        points = [(float(x), float(y)) for x, y in polygon]
        if len(points) >= 3:
            draw.polygon(points, fill=255)

    mask = _expand_mask(mask, expand)
    return _normalize_mask(mask, feather=feather, threshold=threshold)


def _polygon_bounds(polygon: Iterable[tuple[float, float]]) -> tuple[float, float, float, float]:
    points = list(polygon)
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def build_mask_from_detection_boxes(
    size: tuple[int, int],
    detections: Iterable[OCRDetection],
    box_padding: int,
    expand: int,
    feather: int,
    threshold: int,
) -> Image.Image | None:
    rectangles: list[tuple[int, int, int, int]] = []
    width, height = size

    for detection in detections:
        left, top, right, bottom = _polygon_bounds(detection.polygon)
        rectangles.append(
            (
                max(0, int(left) - box_padding),
                max(0, int(top) - box_padding),
                min(width, int(right) + box_padding),
                min(height, int(bottom) + box_padding),
            )
        )

    if not rectangles:
        return None

    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for left, top, right, bottom in rectangles:
        draw.rectangle((left, top, right, bottom), fill=255)

    mask = _expand_mask(mask, expand)
    return _normalize_mask(mask, feather=feather, threshold=threshold)


def render_ocr_preview(image: Image.Image, detections: Iterable[OCRDetection]) -> Image.Image:
    preview = image.convert("RGB").copy()
    draw = ImageDraw.Draw(preview, "RGBA")

    for index, detection in enumerate(detections, start=1):
        points = [(float(x), float(y)) for x, y in detection.polygon]
        if len(points) < 3:
            continue

        color = (255, 64, 64, 255) if detection.matched else (40, 180, 255, 220)
        fill = (255, 64, 64, 45) if detection.matched else (40, 180, 255, 25)
        width = 5 if detection.matched else 2

        draw.polygon(points, outline=color, fill=fill, width=width)

        x0 = min(point[0] for point in points)
        y0 = min(point[1] for point in points)
        badge_box = (x0, max(0, y0 - 18), x0 + 22, max(0, y0 - 18) + 18)
        draw.rectangle(badge_box, fill=color)
        draw.text((badge_box[0] + 5, badge_box[1] + 2), str(index), fill=(255, 255, 255, 255))

    return preview


def load_mask_image(
    mask_path: str | Path,
    size: tuple[int, int],
    expand: int,
    feather: int,
    threshold: int,
    invert: bool,
) -> Image.Image:
    mask = Image.open(mask_path).convert("L").resize(size, Image.Resampling.LANCZOS)
    if invert:
        mask = ImageChops.invert(mask)
    mask = _expand_mask(mask, expand)
    return _normalize_mask(mask, feather=feather, threshold=threshold)


def prepare_mask_image(
    mask: Image.Image,
    size: tuple[int, int],
    expand: int,
    feather: int,
    threshold: int,
    invert: bool,
) -> Image.Image:
    prepared = mask.convert("L").resize(size, Image.Resampling.LANCZOS)
    if invert:
        prepared = ImageChops.invert(prepared)
    prepared = _expand_mask(prepared, expand)
    return _normalize_mask(prepared, feather=feather, threshold=threshold)


def extract_mask_from_editor(
    editor_value: Mapping[str, Any] | None,
    expand: int,
    feather: int,
    threshold: int,
) -> tuple[Image.Image, Image.Image | None]:
    if not editor_value:
        raise ValueError("请先上传原图。")

    background = editor_value.get("background")
    if background is None:
        raise ValueError("请先上传原图。")

    if not isinstance(background, Image.Image):
        raise ValueError("编辑器返回的原图格式不受支持。")

    background = background.convert("RGBA")
    mask_parts: list[Image.Image] = []

    for layer in editor_value.get("layers") or []:
        if not isinstance(layer, Image.Image):
            continue
        alpha = layer.convert("RGBA").resize(background.size, Image.Resampling.LANCZOS).getchannel("A")
        if alpha.getbbox() is not None:
            mask_parts.append(alpha)

    if not mask_parts:
        composite = editor_value.get("composite")
        if isinstance(composite, Image.Image):
            composite = composite.convert("RGBA").resize(background.size, Image.Resampling.LANCZOS)
            diff = ImageChops.difference(composite, background).convert("L")
            if diff.getbbox() is not None:
                mask_parts.append(diff)

    if not mask_parts:
        return background, None

    final_mask = prepare_mask_image(
        mask=merge_masks(mask_parts, background.size),
        size=background.size,
        expand=expand,
        feather=feather,
        threshold=threshold,
        invert=False,
    )
    return background, final_mask


def merge_masks(masks: Iterable[Image.Image], size: tuple[int, int]) -> Image.Image:
    merged = Image.new("L", size, 0)
    for mask in masks:
        merged = ImageChops.lighter(merged, mask.convert("L"))
    return merged


def parse_target_texts(text_items: Iterable[str]) -> list[str]:
    targets: list[str] = []
    for item in text_items:
        for chunk in item.split(";"):
            text = chunk.strip()
            if text:
                targets.append(text)
    return targets


def _normalize_match_text(text: str) -> str:
    return "".join(text.lower().split())


def _text_matches_target(text: str, targets: Iterable[str], match_mode: str) -> bool:
    normalized = _normalize_match_text(text)
    for target in targets:
        normalized_target = _normalize_match_text(target)
        if not normalized_target:
            continue
        if match_mode == "exact" and normalized == normalized_target:
            return True
        if match_mode == "contains" and normalized_target in normalized:
            return True
    return False


@lru_cache(maxsize=1)
def get_ocr_engine():
    if RapidOCR is None:
        raise RuntimeError("缺少 rapidocr 和 onnxruntime 依赖。请先执行: pip install rapidocr onnxruntime")
    return RapidOCR()


def find_text_matches(
    image: Image.Image,
    target_texts: Iterable[str],
    min_score: float,
    match_mode: str,
) -> list[OCRDetection]:
    targets = parse_target_texts(target_texts)
    if not targets:
        return []

    ocr_output = get_ocr_engine()(image.convert("RGB"))
    boxes = getattr(ocr_output, "boxes", None)
    texts = getattr(ocr_output, "txts", None)
    scores = getattr(ocr_output, "scores", None)
    if boxes is None:
        boxes = []
    if texts is None:
        texts = []
    if scores is None:
        scores = []

    detections: list[OCRDetection] = []
    for box, text, score in zip(boxes, texts, scores):
        text = str(text)
        score = float(score)
        polygon = tuple((float(point[0]), float(point[1])) for point in box)
        matched = score >= min_score and _text_matches_target(text, targets, match_mode=match_mode)
        detections.append(OCRDetection(text=text, score=score, polygon=polygon, matched=matched))

    return detections


def build_mask_from_target_text(
    image: Image.Image,
    target_texts: Iterable[str],
    expand: int,
    feather: int,
    threshold: int,
    min_score: float,
    match_mode: str,
    box_padding: int,
) -> tuple[Image.Image | None, list[OCRDetection]]:
    detections = find_text_matches(
        image=image,
        target_texts=target_texts,
        min_score=min_score,
        match_mode=match_mode,
    )
    matches = [detection for detection in detections if detection.matched]
    if not matches:
        return None, detections

    polygon_mask = build_mask_from_polygons(
        size=image.size,
        polygons=[match.polygon for match in matches],
        expand=expand,
        feather=feather,
        threshold=threshold,
    )
    masks = [polygon_mask]
    if box_padding > 0:
        box_mask = build_mask_from_detection_boxes(
            size=image.size,
            detections=matches,
            box_padding=box_padding,
            expand=expand,
            feather=feather,
            threshold=threshold,
        )
        if box_mask is not None:
            masks.append(box_mask)
    return merge_masks(masks, image.size), detections


@lru_cache(maxsize=1)
def get_lama_model():
    if SimpleLama is None:
        raise RuntimeError(
            "缺少 simple-lama-inpainting 依赖。请先执行: pip install -r requirements.txt"
        )
    return SimpleLama()


def _apply_flat_background_smoothing(
    image: Image.Image,
    mask: Image.Image,
    blur_radius: int,
    strength: float,
) -> Image.Image:
    if blur_radius <= 0 or strength <= 0:
        return image

    grayscale_mask = mask.convert("L")
    if grayscale_mask.getbbox() is None:
        return image

    smoothed = image.filter(ImageFilter.GaussianBlur(radius=blur_radius))
    blend_mask = _expand_mask(grayscale_mask, max(2, blur_radius // 3))
    blend_mask = blend_mask.filter(ImageFilter.GaussianBlur(radius=max(1, blur_radius // 2)))
    blend_mask = _scale_mask(blend_mask, strength)
    return Image.composite(smoothed, image, blend_mask)


def run_lama_inpainting(
    image: Image.Image,
    mask: Image.Image,
    flat_bg_mode: bool = False,
    flat_bg_blur: int = 24,
    flat_bg_strength: float = 0.8,
) -> Image.Image:
    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb_image = image.convert("RGB")
    grayscale_mask = mask.convert("L")
    bbox = grayscale_mask.getbbox()
    if bbox is None:
        return image.copy()

    x1, y1, x2, y2 = bbox
    pad = max(32, int(max(x2 - x1, y2 - y1) * 0.6))
    crop_box = (
        max(0, x1 - pad),
        max(0, y1 - pad),
        min(rgb_image.width, x2 + pad),
        min(rgb_image.height, y2 + pad),
    )

    crop_image = rgb_image.crop(crop_box)
    crop_mask = grayscale_mask.crop(crop_box)
    crop_result = get_lama_model()(crop_image, crop_mask)
    if isinstance(crop_result, np.ndarray):
        crop_result = Image.fromarray(crop_result)
    if crop_result.size != crop_mask.size:
        crop_result = crop_result.resize(crop_mask.size, Image.Resampling.LANCZOS)
    crop_result = crop_result.convert("RGB")
    if flat_bg_mode:
        crop_result = _apply_flat_background_smoothing(
            image=crop_result,
            mask=crop_mask,
            blur_radius=flat_bg_blur,
            strength=flat_bg_strength,
        )

    result = rgb_image.copy()
    result.paste(crop_result, crop_box[:2])
    if alpha is None:
        return result

    rgba_result = result.convert("RGBA")
    rgba_result.putalpha(alpha)
    return rgba_result
