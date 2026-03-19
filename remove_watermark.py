#!/usr/bin/env python3
import argparse
from pathlib import Path
from typing import Optional

import cv2
import numpy as np


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="去水印工具：mode=1 适合角落水印，mode=2 适合平铺水印。"
    )
    parser.add_argument("-i", "--input", required=True, help="输入图片路径")
    parser.add_argument("-o", "--output", required=True, help="输出图片路径")
    parser.add_argument(
        "-m",
        "--mode",
        required=True,
        type=int,
        choices=[1, 2],
        help="1=角落水印，2=平铺水印",
    )
    parser.add_argument(
        "--mask-output",
        help="可选：输出识别到的水印掩码，便于调参",
    )
    parser.add_argument(
        "--corner-ratio",
        type=float,
        default=0.24,
        help="mode=1 时每个角落参与检测的区域比例，默认 0.24",
    )
    parser.add_argument(
        "--strength",
        type=float,
        default=1.0,
        help="掩码强度系数，越大越激进，默认 1.0",
    )
    parser.add_argument(
        "--corner",
        choices=["all", "top-left", "top-right", "bottom-left", "bottom-right"],
        default="all",
        help="mode=1 时指定处理哪个角，默认 all",
    )
    parser.add_argument(
        "--roi",
        help="可选：手动指定处理区域，格式 x,y,w,h；mode=1 时会优先使用该区域",
    )
    return parser.parse_args()


def ensure_uint8_mask(mask: np.ndarray) -> np.ndarray:
    mask = np.where(mask > 0, 255, 0).astype(np.uint8)
    return mask


def remove_small_components(mask: np.ndarray, min_area: int) -> np.ndarray:
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    filtered = np.zeros_like(mask)
    for label in range(1, num_labels):
        area = stats[label, cv2.CC_STAT_AREA]
        if area >= min_area:
            filtered[labels == label] = 255
    return filtered


def component_boxes(mask: np.ndarray, min_area: int) -> list[tuple[int, int, int, int, int]]:
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    boxes: list[tuple[int, int, int, int, int]] = []
    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        boxes.append((x, y, w, h, area))
    return boxes


def percentile_threshold(arr: np.ndarray, q: float, fallback: int = 0) -> int:
    if arr.size == 0:
        return fallback
    return int(np.percentile(arr, q))


def parse_roi(roi_text: Optional[str], width: int, height: int) -> Optional[tuple[int, int, int, int]]:
    if not roi_text:
        return None
    parts = [part.strip() for part in roi_text.split(",")]
    if len(parts) != 4:
        raise SystemExit("--roi 格式必须是 x,y,w,h")

    try:
        x, y, w, h = [int(part) for part in parts]
    except ValueError as exc:
        raise SystemExit("--roi 必须是整数: x,y,w,h") from exc

    if w <= 0 or h <= 0:
        raise SystemExit("--roi 的 w 和 h 必须大于 0")

    x1 = max(0, x)
    y1 = max(0, y)
    x2 = min(width, x1 + w)
    y2 = min(height, y1 + h)
    if x2 <= x1 or y2 <= y1:
        raise SystemExit("--roi 超出图片范围")

    return x1, y1, x2, y2


def component_mask_from_seed(seed_mask: np.ndarray, candidate_mask: np.ndarray) -> np.ndarray:
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(candidate_mask, 8)
    keep = np.zeros_like(candidate_mask)
    min_area = max(12, int(candidate_mask.size * 0.00025))
    max_area = max(min_area + 1, int(candidate_mask.size * 0.45))

    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue
        component = labels == label
        if np.any(seed_mask[component] > 0):
            keep[component] = 255

    return keep


def expand_seed_mask(mask: np.ndarray, kernel_size: int, blur_sigma: float, threshold: int) -> np.ndarray:
    expanded = cv2.dilate(
        mask,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)),
        iterations=1,
    )
    expanded = cv2.GaussianBlur(expanded, (0, 0), blur_sigma)
    _, expanded = cv2.threshold(expanded, threshold, 255, cv2.THRESH_BINARY)
    return expanded.astype(np.uint8)


def odd_kernel(size: int) -> int:
    return size if size % 2 == 1 else size + 1


def shifted_mask(mask: np.ndarray, dx: int, dy: int) -> np.ndarray:
    matrix = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(mask, matrix, (mask.shape[1], mask.shape[0]))


def filter_corner_components(mask: np.ndarray, corner_name: str) -> np.ndarray:
    roi_h, roi_w = mask.shape[:2]
    keep = np.zeros_like(mask)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    edge_margin = max(8, min(roi_w, roi_h) // 7)
    max_area = int(roi_w * roi_h * 0.08)

    for label in range(1, num_labels):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area <= 0 or area > max_area:
            continue

        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])

        touches_outer_edge = False
        if corner_name == "top-left":
            touches_outer_edge = x <= edge_margin or y <= edge_margin
        elif corner_name == "top-right":
            touches_outer_edge = x + w >= roi_w - edge_margin or y <= edge_margin
        elif corner_name == "bottom-left":
            touches_outer_edge = x <= edge_margin or y + h >= roi_h - edge_margin
        else:
            touches_outer_edge = x + w >= roi_w - edge_margin or y + h >= roi_h - edge_margin

        if touches_outer_edge:
            keep[labels == label] = 255

    return keep


def build_low_contrast_mask(roi: np.ndarray, strength: float) -> np.ndarray:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    kernel_size = odd_kernel(max(15, min(roi.shape[:2]) // 8))
    background = cv2.morphologyEx(
        gray,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)),
    )
    background = cv2.GaussianBlur(background, (0, 0), max(3.0, kernel_size / 6.0))
    bright_diff = cv2.subtract(gray, background)

    value_thresh = percentile_threshold(val, max(68, 84 - int(strength * 8)), 220)
    seed_thresh = percentile_threshold(bright_diff, max(95, 99 - int(strength * 3)), 6)
    grow_thresh = percentile_threshold(bright_diff, max(80, 90 - int(strength * 6)), 3)
    sat_thresh = percentile_threshold(sat, min(45, 24 + int(strength * 10)), 28)

    seed = np.logical_and(bright_diff >= max(4, seed_thresh), val >= max(230, value_thresh))
    seed = np.logical_and(seed, sat <= max(20, sat_thresh))

    candidate = np.logical_and(bright_diff >= max(2, grow_thresh), val >= max(210, value_thresh - 10))
    candidate = np.logical_and(candidate, sat <= max(28, sat_thresh + 6))

    seed_mask = ensure_uint8_mask(seed)
    candidate_mask = ensure_uint8_mask(candidate)
    component_mask = component_mask_from_seed(seed_mask, candidate_mask)
    component_mask = cv2.morphologyEx(
        component_mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    )
    text_mask = expand_seed_mask(component_mask, 17, 6.0, 14)

    shadow_seed = cv2.dilate(
        shifted_mask(text_mask, dx=max(2, int(3 * strength)), dy=max(6, int(8 * strength))),
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 11)),
        iterations=1,
    )
    shadow_seed = cv2.GaussianBlur(shadow_seed, (0, 0), 6.0)
    _, shadow_seed = cv2.threshold(shadow_seed, 20, 255, cv2.THRESH_BINARY)

    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    shadow_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (odd_kernel(max(15, roi.shape[1] // 18)), 9))
    local_bg = cv2.morphologyEx(gray, cv2.MORPH_OPEN, shadow_kernel)
    dark_diff = cv2.subtract(local_bg, gray)
    shadow_candidate = ensure_uint8_mask(dark_diff >= max(2, percentile_threshold(dark_diff, 70, 3)))
    shadow_candidate = cv2.morphologyEx(
        shadow_candidate, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 7))
    )
    shadow_mask = ensure_uint8_mask(np.logical_and(shadow_seed > 0, shadow_candidate > 0))

    return ensure_uint8_mask(np.logical_or(text_mask > 0, shadow_mask > 0))


def estimate_smooth_background(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    k = odd_kernel(max(21, min(image.shape[:2]) // 7))
    sigma = max(4.0, k / 5.0)
    channels = []
    for channel in cv2.split(image):
        opened = cv2.morphologyEx(
            channel,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)),
        )
        channels.append(cv2.GaussianBlur(opened, (0, 0), sigma))
    background = cv2.merge(channels)

    if np.count_nonzero(mask) == 0:
        return background

    softened_mask = expand_seed_mask(mask, 21, 8.0, 10)
    alpha = cv2.GaussianBlur(softened_mask.astype(np.float32) / 255.0, (0, 0), 6.0)
    alpha = np.clip(alpha[..., None], 0.0, 1.0)
    blended = image.astype(np.float32) * (1.0 - alpha) + background.astype(np.float32) * alpha
    return np.clip(blended, 0, 255).astype(np.uint8)


def mask_bounding_box(mask: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    ys, xs = np.where(mask > 0)
    if xs.size == 0 or ys.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def adjust_patch_to_target(
    image: np.ndarray,
    patch: np.ndarray,
    target_box: tuple[int, int, int, int],
) -> np.ndarray:
    x1, y1, x2, y2 = target_box
    band = max(8, min(y2 - y1, x2 - x1) // 8)
    deltas = []

    if y1 - band >= 0:
        target_top = image[y1 - band:y1, x1:x2]
        patch_top = patch[:band, :]
        deltas.append(target_top.reshape(-1, 3).mean(axis=0) - patch_top.reshape(-1, 3).mean(axis=0))
    if y2 + band <= image.shape[0]:
        target_bottom = image[y2:y2 + band, x1:x2]
        patch_bottom = patch[-band:, :]
        deltas.append(target_bottom.reshape(-1, 3).mean(axis=0) - patch_bottom.reshape(-1, 3).mean(axis=0))
    if x1 - band >= 0:
        target_left = image[y1:y2, x1 - band:x1]
        patch_left = patch[:, :band]
        deltas.append(target_left.reshape(-1, 3).mean(axis=0) - patch_left.reshape(-1, 3).mean(axis=0))
    if x2 + band <= image.shape[1]:
        target_right = image[y1:y2, x2:x2 + band]
        patch_right = patch[:, -band:]
        deltas.append(target_right.reshape(-1, 3).mean(axis=0) - patch_right.reshape(-1, 3).mean(axis=0))

    if not deltas:
        return patch

    delta = np.mean(deltas, axis=0)
    adjusted = np.clip(patch.astype(np.float32) + delta, 0, 255)
    return adjusted.astype(np.uint8)


def nearby_patch_candidates(
    target_box: tuple[int, int, int, int],
    image_shape: tuple[int, int, int],
) -> list[tuple[int, int, int, int]]:
    x1, y1, x2, y2 = target_box
    h = y2 - y1
    w = x2 - x1
    img_h, img_w = image_shape[:2]
    corner = infer_corner(x1, y1, x2, y2, img_w, img_h)
    gaps = [20, 35, 50, 70]
    boxes: list[tuple[int, int, int, int]] = []

    if corner == "bottom-right":
        for gap in gaps:
            boxes.append((x1, y1 - h - gap, x2, y1 - gap))
            boxes.append((x1 - w - gap, y1, x1 - gap, y2))
            boxes.append((x1 - w - gap, y1 - h - gap, x1 - gap, y1 - gap))
    elif corner == "bottom-left":
        for gap in gaps:
            boxes.append((x1, y1 - h - gap, x2, y1 - gap))
            boxes.append((x2 + gap, y1, x2 + w + gap, y2))
            boxes.append((x2 + gap, y1 - h - gap, x2 + w + gap, y1 - gap))
    elif corner == "top-right":
        for gap in gaps:
            boxes.append((x1, y2 + gap, x2, y2 + h + gap))
            boxes.append((x1 - w - gap, y1, x1 - gap, y2))
            boxes.append((x1 - w - gap, y2 + gap, x1 - gap, y2 + h + gap))
    else:
        for gap in gaps:
            boxes.append((x1, y2 + gap, x2, y2 + h + gap))
            boxes.append((x2 + gap, y1, x2 + w + gap, y2))
            boxes.append((x2 + gap, y2 + gap, x2 + w + gap, y2 + h + gap))

    valid: list[tuple[int, int, int, int]] = []
    for sx1, sy1, sx2, sy2 in boxes:
        if sx1 < 0 or sy1 < 0 or sx2 > img_w or sy2 > img_h:
            continue
        valid.append((sx1, sy1, sx2, sy2))
    return valid


def expanded_replacement_box(
    mask: np.ndarray,
    image_shape: tuple[int, int, int],
) -> Optional[tuple[int, int, int, int]]:
    bbox = mask_bounding_box(mask)
    if bbox is None:
        return None

    x1, y1, x2, y2 = bbox
    img_h, img_w = image_shape[:2]
    corner = infer_corner(x1, y1, x2, y2, img_w, img_h)

    left_pad = 18
    right_pad = 18
    top_pad = 18
    bottom_pad = 18

    if corner == "bottom-right":
        left_pad, right_pad, top_pad, bottom_pad = 18, 22, 18, 52
    elif corner == "bottom-left":
        left_pad, right_pad, top_pad, bottom_pad = 22, 18, 18, 52
    elif corner == "top-right":
        left_pad, right_pad, top_pad, bottom_pad = 18, 22, 52, 18
    else:
        left_pad, right_pad, top_pad, bottom_pad = 22, 18, 52, 18

    return clip_box(x1 - left_pad, y1 - top_pad, x2 + right_pad, y2 + bottom_pad, img_w, img_h)


def search_ranges_for_corner(
    target_box: tuple[int, int, int, int],
    image_shape: tuple[int, int, int],
) -> tuple[range, range]:
    x1, y1, x2, y2 = target_box
    h = y2 - y1
    w = x2 - x1
    img_h, img_w = image_shape[:2]
    corner = infer_corner(x1, y1, x2, y2, img_w, img_h)

    if corner == "bottom-right":
        x_range = range(max(0, x1 - 220), min(img_w - w, x1 + 60) + 1, 2)
        y_range = range(max(0, y1 - 220), max(0, y1 - 30) + 1, 2)
    elif corner == "bottom-left":
        x_range = range(max(0, x2 + 30), min(img_w - w, x2 + 220) + 1, 2)
        y_range = range(max(0, y1 - 220), max(0, y1 - 30) + 1, 2)
    elif corner == "top-right":
        x_range = range(max(0, x1 - 220), min(img_w - w, x1 + 60) + 1, 2)
        y_range = range(min(img_h - h, y2 + 30), min(img_h - h, y2 + 220) + 1, 2)
    else:
        x_range = range(max(0, x2 + 30), min(img_w - w, x2 + 220) + 1, 2)
        y_range = range(min(img_h - h, y2 + 30), min(img_h - h, y2 + 220) + 1, 2)

    return x_range, y_range


def replace_from_nearby_patch(image: np.ndarray, mask: np.ndarray) -> Optional[np.ndarray]:
    target_box = expanded_replacement_box(mask, image.shape)
    if target_box is None:
        return image.copy()

    best_score = float("inf")
    best_patch: Optional[np.ndarray] = None
    x1, y1, x2, y2 = target_box
    h = y2 - y1
    w = x2 - x1
    x_range, y_range = search_ranges_for_corner(target_box, image.shape)

    for sy1 in y_range:
        sy2 = sy1 + h
        if sy2 > image.shape[0]:
            continue
        for sx1 in x_range:
            sx2 = sx1 + w
            if sx2 > image.shape[1]:
                continue
            overlap = mask[sy1:sy2, sx1:sx2]
            if np.count_nonzero(overlap) > overlap.size * 0.01:
                continue
            source_box = (sx1, sy1, sx2, sy2)
            score = score_source_patch(image, target_box, source_box)
            if score < best_score:
                best_score = score
                best_patch = image[sy1:sy2, sx1:sx2].copy()

    if best_patch is None:
        return None

    best_patch = adjust_patch_to_target(image, best_patch, target_box)
    x1, y1, x2, y2 = target_box
    local_mask = mask[y1:y2, x1:x2]
    blend_mask = cv2.dilate(
        local_mask,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)),
        iterations=1,
    )
    alpha = cv2.GaussianBlur(blend_mask.astype(np.float32) / 255.0, (0, 0), 16.0)
    alpha = np.clip(alpha[..., None], 0.0, 1.0)
    result = image.copy().astype(np.float32)
    result[y1:y2, x1:x2] = (
        result[y1:y2, x1:x2] * (1.0 - alpha) + best_patch.astype(np.float32) * alpha
    )
    return np.clip(result, 0, 255).astype(np.uint8)


def smooth_shadow_band(image: np.ndarray, text_mask: np.ndarray) -> np.ndarray:
    bbox = mask_bounding_box(text_mask)
    if bbox is None:
        return image

    x1, y1, x2, y2 = bbox
    bx1 = max(0, x1 - 8)
    bx2 = min(image.shape[1], x2 + 10)
    by1 = min(image.shape[0], y1 + int((y2 - y1) * 0.60))
    by2 = min(image.shape[0], y2 + 10)

    if by1 <= 14 or by2 + 14 >= image.shape[0] or bx2 <= bx1:
        return image

    result = image.astype(np.float32).copy()
    src_top = result[by1 - 14:by1, bx1:bx2].mean(axis=0)
    src_bottom = result[by2:by2 + 14, bx1:bx2].mean(axis=0)
    height = max(1, by2 - by1)

    for i, yy in enumerate(range(by1, by2)):
        t = (i + 0.5) / height
        target = src_top * (1.0 - t) + src_bottom * t
        alpha = 0.75
        result[yy, bx1:bx2] = result[yy, bx1:bx2] * (1.0 - alpha) + target * alpha

    for extra in range(1, 10):
        alpha = 0.75 * (1.0 - extra / 10.0)
        yy_top = by1 - extra
        yy_bottom = by2 + extra - 1
        if yy_top >= 0:
            result[yy_top, bx1:bx2] = result[yy_top, bx1:bx2] * (1.0 - alpha) + src_top * alpha
        if yy_bottom < image.shape[0]:
            result[yy_bottom, bx1:bx2] = result[yy_bottom, bx1:bx2] * (1.0 - alpha) + src_bottom * alpha

    return np.clip(result, 0, 255).astype(np.uint8)


def build_corner_roi_mask(roi: np.ndarray, corner_name: str, strength: float) -> np.ndarray:
    mask = build_low_contrast_mask(roi, strength)
    return filter_corner_components(mask, corner_name)


def build_roi_mask(image: np.ndarray, roi_box: tuple[int, int, int, int], strength: float) -> np.ndarray:
    x1, y1, x2, y2 = roi_box
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    roi = image[y1:y2, x1:x2]
    roi_mask = build_low_contrast_mask(roi, strength)
    mask[y1:y2, x1:x2] = roi_mask
    return mask


def build_corner_mask(
    image: np.ndarray,
    corner_ratio: float,
    strength: float,
    selected_corner: str,
) -> np.ndarray:
    h, w = image.shape[:2]
    corner_h = max(24, int(h * corner_ratio))
    corner_w = max(24, int(w * corner_ratio))
    mask = np.zeros((h, w), dtype=np.uint8)

    corners = [
        ("top-left", 0, corner_h, 0, corner_w),
        ("top-right", 0, corner_h, w - corner_w, w),
        ("bottom-left", h - corner_h, h, 0, corner_w),
        ("bottom-right", h - corner_h, h, w - corner_w, w),
    ]

    for corner_name, y1, y2, x1, x2 in corners:
        if selected_corner != "all" and corner_name != selected_corner:
            continue
        roi = image[y1:y2, x1:x2]
        roi_mask = build_corner_roi_mask(roi, corner_name, strength)
        mask[y1:y2, x1:x2] = np.maximum(mask[y1:y2, x1:x2], roi_mask)

    return mask


def clip_box(x1: int, y1: int, x2: int, y2: int, width: int, height: int) -> tuple[int, int, int, int]:
    return max(0, x1), max(0, y1), min(width, x2), min(height, y2)


def expand_box(x: int, y: int, w: int, h: int, width: int, height: int, pad: int) -> tuple[int, int, int, int]:
    return clip_box(x - pad, y - pad, x + w + pad, y + h + pad, width, height)


def infer_corner(x1: int, y1: int, x2: int, y2: int, width: int, height: int) -> str:
    center_x = (x1 + x2) / 2.0
    center_y = (y1 + y2) / 2.0
    horizontal = "left" if center_x < width / 2 else "right"
    vertical = "top" if center_y < height / 2 else "bottom"
    return f"{vertical}-{horizontal}"


def border_difference(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0:
        return 0.0
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    if h <= 0 or w <= 0:
        return 0.0
    diff = a[:h, :w].astype(np.float32) - b[:h, :w].astype(np.float32)
    return float(np.mean(np.abs(diff)))


def score_source_patch(
    image: np.ndarray,
    target_box: tuple[int, int, int, int],
    source_box: tuple[int, int, int, int],
) -> float:
    x1, y1, x2, y2 = target_box
    sx1, sy1, sx2, sy2 = source_box
    target_h = y2 - y1
    target_w = x2 - x1
    patch = image[sy1:sy2, sx1:sx2]
    band = max(3, min(target_h, target_w) // 10)
    scores: list[float] = []

    if y1 - band >= 0:
        scores.append(border_difference(patch[:band, :], image[y1 - band:y1, x1:x2]))
    if y2 + band <= image.shape[0]:
        scores.append(border_difference(patch[-band:, :], image[y2:y2 + band, x1:x2]))
    if x1 - band >= 0:
        scores.append(border_difference(patch[:, :band], image[y1:y2, x1 - band:x1]))
    if x2 + band <= image.shape[1]:
        scores.append(border_difference(patch[:, -band:], image[y1:y2, x2:x2 + band]))

    if not scores:
        return float("inf")

    distance_penalty = 0.015 * (abs(sx1 - x1) + abs(sy1 - y1))
    return float(np.mean(scores)) + distance_penalty


def candidate_offsets(width: int, height: int, corner: str) -> list[tuple[int, int]]:
    x_sign = 1 if "left" in corner else -1
    y_sign = 1 if "top" in corner else -1
    x_steps = [int(x_sign * width * scale) for scale in (0.8, 1.1, 1.5, 2.0, 2.6)]
    y_steps = [int(y_sign * height * scale) for scale in (0.8, 1.1, 1.5, 2.0, 2.6)]
    offsets: list[tuple[int, int]] = []

    for dx in x_steps:
        offsets.append((dx, 0))
    for dy in y_steps:
        offsets.append((0, dy))
    for dx in x_steps:
        for dy in y_steps:
            offsets.append((dx, dy))

    return offsets


def find_source_patch(
    image: np.ndarray,
    mask: np.ndarray,
    target_box: tuple[int, int, int, int],
) -> Optional[tuple[int, int, int, int]]:
    x1, y1, x2, y2 = target_box
    width = x2 - x1
    height = y2 - y1
    img_h, img_w = image.shape[:2]
    corner = infer_corner(x1, y1, x2, y2, img_w, img_h)

    best_box: Optional[tuple[int, int, int, int]] = None
    best_score = float("inf")

    for dx, dy in candidate_offsets(width, height, corner):
        sx1 = x1 + dx
        sy1 = y1 + dy
        sx2 = sx1 + width
        sy2 = sy1 + height
        if sx1 < 0 or sy1 < 0 or sx2 > img_w or sy2 > img_h:
            continue

        overlap = mask[sy1:sy2, sx1:sx2]
        if np.count_nonzero(overlap) > overlap.size * 0.02:
            continue

        score = score_source_patch(image, target_box, (sx1, sy1, sx2, sy2))
        if score < best_score:
            best_score = score
            best_box = (sx1, sy1, sx2, sy2)

    return best_box


def blend_patch(
    image: np.ndarray,
    patch: np.ndarray,
    local_mask: np.ndarray,
    target_box: tuple[int, int, int, int],
) -> None:
    x1, y1, x2, y2 = target_box
    work = image[y1:y2, x1:x2].astype(np.float32)
    patch_f = patch.astype(np.float32)

    blend_mask = cv2.dilate(
        local_mask,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11)),
        iterations=1,
    )
    alpha = cv2.GaussianBlur(blend_mask.astype(np.float32) / 255.0, (0, 0), 5)
    alpha = np.clip(alpha[..., None], 0.0, 1.0)

    blended = work * (1.0 - alpha) + patch_f * alpha
    image[y1:y2, x1:x2] = np.clip(blended, 0, 255).astype(np.uint8)


def restore_corner_regions(image: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    restored = image.copy()
    fallback_mask = np.zeros_like(mask)
    img_h, img_w = image.shape[:2]
    boxes = component_boxes(mask, min_area=max(10, int(mask.size * 0.00005)))
    boxes.sort(key=lambda item: item[4], reverse=True)

    for x, y, w, h, area in boxes:
        pad = max(10, int(max(w, h) * 0.35))
        x1, y1, x2, y2 = expand_box(x, y, w, h, img_w, img_h, pad)
        local_mask = mask[y1:y2, x1:x2]
        source_box = find_source_patch(restored, mask, (x1, y1, x2, y2))

        if source_box is None or area < 120:
            fallback_mask[y1:y2, x1:x2] = np.maximum(fallback_mask[y1:y2, x1:x2], local_mask)
            continue

        sx1, sy1, sx2, sy2 = source_box
        patch = restored[sy1:sy2, sx1:sx2].copy()
        blend_patch(restored, patch, local_mask, (x1, y1, x2, y2))

    return restored, fallback_mask


def build_tiled_mask(image: np.ndarray, strength: float) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]

    large_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (19, 19))
    medium_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))

    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, large_kernel)
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, large_kernel)
    detail = np.maximum(tophat, blackhat)

    grad_x = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3)
    abs_grad_x = cv2.convertScaleAbs(grad_x)
    abs_grad_y = cv2.convertScaleAbs(grad_y)
    gradient = cv2.addWeighted(abs_grad_x, 0.5, abs_grad_y, 0.5, 0)

    detail_thresh = percentile_threshold(detail, max(88, 95 - 6 * strength), 18)
    grad_thresh = percentile_threshold(gradient, max(86, 93 - 6 * strength), 16)
    sat_thresh = percentile_threshold(sat, min(85, 62 + 12 * strength), 90)

    candidate = np.logical_and(detail > detail_thresh, sat < sat_thresh)
    candidate = np.logical_or(candidate, np.logical_and(gradient > grad_thresh, sat < sat_thresh))
    mask = ensure_uint8_mask(candidate)

    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, medium_kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, medium_kernel)
    mask = cv2.dilate(
        mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1
    )

    min_area = max(18, int(image.shape[0] * image.shape[1] * 0.00008))
    mask = remove_small_components(mask, min_area)

    mask_ratio = float(np.count_nonzero(mask)) / float(mask.size)
    if mask_ratio > 0.22:
        stricter = detail > percentile_threshold(detail, 98, detail_thresh + 5)
        mask = ensure_uint8_mask(np.logical_and(stricter, sat < sat_thresh))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, medium_kernel)
        mask = cv2.dilate(
            mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1
        )
        mask = remove_small_components(mask, min_area)

    return mask


def inpaint_image(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    return cv2.inpaint(image, mask, 5, cv2.INPAINT_TELEA)


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    corner_ratio = min(max(args.corner_ratio, 0.05), 0.45)
    strength = max(args.strength, 0.2)

    image = cv2.imread(str(input_path), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"无法读取图片: {input_path}")
    roi_box = parse_roi(args.roi, image.shape[1], image.shape[0])

    if args.mode == 1:
        mask = build_roi_mask(image, roi_box, strength) if roi_box else build_corner_mask(
            image, corner_ratio, strength, args.corner
        )
        result = replace_from_nearby_patch(image, mask)
        if result is None:
            result = estimate_smooth_background(image, mask)
        result = smooth_shadow_band(result, mask)
    else:
        mask = build_tiled_mask(image, strength)
        result = inpaint_image(image, mask)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output_path), result):
        raise SystemExit(f"输出失败: {output_path}")

    if args.mask_output:
        mask_output = Path(args.mask_output)
        mask_output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(mask_output), mask):
            raise SystemExit(f"掩码输出失败: {mask_output}")

    print(f"已完成，输出文件: {output_path}")
    print(f"模式: {args.mode}")
    print(f"识别掩码像素占比: {np.count_nonzero(mask) / mask.size:.4f}")


if __name__ == "__main__":
    main()
