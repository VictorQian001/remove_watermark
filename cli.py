#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

try:
    from .inpaint_core import (
        build_mask_from_target_text,
        build_mask_from_rois,
        load_mask_image,
        merge_masks,
        parse_rois,
        parse_target_texts,
        render_ocr_preview,
        run_lama_inpainting,
    )
except ImportError:
    from inpaint_core import (
        build_mask_from_target_text,
        build_mask_from_rois,
        load_mask_image,
        merge_masks,
        parse_rois,
        parse_target_texts,
        render_ocr_preview,
        run_lama_inpainting,
    )


SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="基于 LaMa 的 AI 去水印工具，适合复杂纹理背景。"
    )
    parser.add_argument("-i", "--input", required=True, help="输入图片路径")
    parser.add_argument("-o", "--output", required=True, help="输出图片路径")
    parser.add_argument(
        "--mask",
        help="可选：掩码图片路径。白色区域会被 AI 补全，黑色区域保持不变。",
    )
    parser.add_argument(
        "--roi",
        action="append",
        default=[],
        help="可选：矩形区域，格式 x,y,w,h。可重复传入，也可用分号连接多个。",
    )
    parser.add_argument(
        "--mask-output",
        help="可选：输出合成后的最终掩码图，便于检查覆盖范围。",
    )
    parser.add_argument(
        "--ocr-preview",
        help="可选：导出 OCR 检测框预览图。单图模式传文件路径，目录模式传输出目录。",
    )
    parser.add_argument(
        "--target-text",
        action="append",
        default=[],
        help="可选：要去除的水印文字。可重复传入，也可用分号连接多个。",
    )
    parser.add_argument(
        "--text-match-mode",
        choices=["contains", "exact"],
        default="contains",
        help="OCR 文本匹配方式，默认 contains。",
    )
    parser.add_argument(
        "--ocr-min-score",
        type=float,
        default=0.5,
        help="OCR 最低置信度，默认 0.5。",
    )
    parser.add_argument(
        "--expand",
        type=int,
        default=12,
        help="对掩码做外扩，单位像素，默认 12。",
    )
    parser.add_argument(
        "--feather",
        type=int,
        default=6,
        help="对掩码边缘先模糊再二值化，帮助略微放宽边界，默认 6。",
    )
    parser.add_argument(
        "--mask-threshold",
        type=int,
        default=32,
        help="掩码二值化阈值，默认 32，范围 0-255。",
    )
    parser.add_argument(
        "--invert-mask",
        action="store_true",
        help="如果传入的掩码黑白含义相反，可打开此选项。",
    )
    return parser.parse_args()


def process_single_image(
    input_path: Path,
    output_path: Path,
    args: argparse.Namespace,
    mask_output_path: Path | None,
    ocr_preview_path: Path | None,
) -> None:
    image = Image.open(input_path)
    width, height = image.size
    mask_parts = []

    if args.mask:
        mask_parts.append(
            load_mask_image(
                mask_path=args.mask,
                size=image.size,
                expand=args.expand,
                feather=args.feather,
                threshold=args.mask_threshold,
                invert=args.invert_mask,
            )
        )

    target_texts = parse_target_texts(args.target_text)
    if target_texts:
        try:
            text_mask, detections = build_mask_from_target_text(
                image=image,
                target_texts=target_texts,
                expand=args.expand,
                feather=args.feather,
                threshold=args.mask_threshold,
                min_score=args.ocr_min_score,
                match_mode=args.text_match_mode,
            )
        except RuntimeError as exc:
            raise SystemExit(str(exc)) from exc

        if ocr_preview_path is not None:
            ocr_preview_path.parent.mkdir(parents=True, exist_ok=True)
            render_ocr_preview(image, detections).save(ocr_preview_path)

        matches = [detection for detection in detections if detection.matched]
        if text_mask is None:
            recognized_texts = [detection.text for detection in detections]
            recognized_summary = "、".join(recognized_texts[:12]) if recognized_texts else "无"
            raise SystemExit(
                f"OCR 没有匹配到目标文字: {', '.join(target_texts)}。当前识别到的文字: {recognized_summary}"
            )
        mask_parts.append(text_mask)
        print(
            f"[OCR] {input_path.name}: matched {len(matches)} item(s): "
            + ", ".join(f"{match.text}({match.score:.2f})" for match in matches)
        )

    if args.roi:
        try:
            rois = parse_rois(args.roi, width=width, height=height)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        mask_parts.append(
            build_mask_from_rois(
                size=image.size,
                rois=rois,
                expand=args.expand,
                feather=args.feather,
                threshold=args.mask_threshold,
            )
        )

    final_mask = merge_masks(mask_parts, size=image.size)
    if final_mask.getbbox() is None:
        raise SystemExit("最终掩码为空，没有可修复区域。")

    try:
        result = run_lama_inpainting(image, final_mask)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(output_path)
    if mask_output_path is not None:
        mask_output_path.parent.mkdir(parents=True, exist_ok=True)
        final_mask.save(mask_output_path)


def iter_input_images(input_dir: Path) -> list[Path]:
    return sorted(
        path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not 0 <= args.mask_threshold <= 255:
        raise SystemExit("--mask-threshold 必须在 0 到 255 之间。")
    if not 0.0 <= args.ocr_min_score <= 1.0:
        raise SystemExit("--ocr-min-score 必须在 0 到 1 之间。")
    if not args.mask and not args.roi and not args.target_text:
        raise SystemExit("至少要提供 --mask、--roi 或 --target-text 其中之一。")

    if input_path.is_dir():
        if args.mask:
            raise SystemExit("批处理目录模式下不支持单个 --mask 文件，请改用 --target-text 或 --roi。")
        images = iter_input_images(input_path)
        if not images:
            raise SystemExit("输入目录里没有找到支持的图片文件。")

        output_path.mkdir(parents=True, exist_ok=True)
        mask_output_dir = Path(args.mask_output) if args.mask_output else None
        if mask_output_dir is not None:
            mask_output_dir.mkdir(parents=True, exist_ok=True)

        for image_path in images:
            result_path = output_path / image_path.name
            current_mask_output = None
            if mask_output_dir is not None:
                current_mask_output = mask_output_dir / f"{image_path.stem}_mask.png"
            current_ocr_preview = None
            if args.ocr_preview:
                current_ocr_preview = Path(args.ocr_preview) / f"{image_path.stem}_ocr_preview.png"
                current_ocr_preview.parent.mkdir(parents=True, exist_ok=True)
            try:
                process_single_image(image_path, result_path, args, current_mask_output, current_ocr_preview)
            except SystemExit as exc:
                print(f"[WARN] {image_path.name}: {exc}")
        return

    mask_output_path = Path(args.mask_output) if args.mask_output else None
    ocr_preview_path = Path(args.ocr_preview) if args.ocr_preview else None
    process_single_image(input_path, output_path, args, mask_output_path, ocr_preview_path)


if __name__ == "__main__":
    main()
