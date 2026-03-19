#!/usr/bin/env python3
from __future__ import annotations

import os
import time

import gradio as gr

try:
    from .inpaint_core import (
        build_mask_from_target_text,
        build_mask_from_rois,
        extract_mask_from_editor,
        merge_masks,
        parse_rois,
        parse_target_texts,
        run_lama_inpainting,
    )
except ImportError:
    from inpaint_core import (
        build_mask_from_target_text,
        build_mask_from_rois,
        extract_mask_from_editor,
        merge_masks,
        parse_rois,
        parse_target_texts,
        run_lama_inpainting,
    )


def build_final_mask(
    editor_value,
    target_text: str,
    roi_text: str,
    expand: int,
    feather: int,
    threshold: int,
    ocr_min_score: float,
    ocr_box_padding: int,
    text_match_mode: str,
):
    try:
        image, editor_mask = extract_mask_from_editor(
            editor_value=editor_value,
            expand=expand,
            feather=feather,
            threshold=threshold,
        )
    except ValueError as exc:
        raise gr.Error(str(exc)) from exc

    masks = []
    if editor_mask is not None:
        masks.append(editor_mask)

    target_texts = parse_target_texts([target_text]) if target_text.strip() else []
    if target_texts:
        try:
            text_mask, detections = build_mask_from_target_text(
                image=image,
                target_texts=target_texts,
                expand=expand,
                feather=feather,
                threshold=threshold,
                min_score=ocr_min_score,
                match_mode=text_match_mode,
                box_padding=ocr_box_padding,
            )
        except RuntimeError as exc:
            raise gr.Error(str(exc)) from exc
        if text_mask is None:
            recognized_texts = [detection.text for detection in detections]
            recognized_summary = "、".join(recognized_texts[:12]) if recognized_texts else "无"
            raise gr.Error(
                f"OCR 没有匹配到目标文字: {', '.join(target_texts)}。当前识别到的文字: {recognized_summary}"
            )
        masks.append(text_mask)

    if roi_text.strip():
        try:
            rois = parse_rois([roi_text], width=image.width, height=image.height)
        except ValueError as exc:
            raise gr.Error(str(exc)) from exc
        masks.append(
            build_mask_from_rois(
                size=image.size,
                rois=rois,
                expand=expand,
                feather=feather,
                threshold=threshold,
            )
        )

    if not masks:
        raise gr.Error("请直接在网页上涂抹白色掩码，或者填写 ROI。")

    final_mask = merge_masks(masks, size=image.size)
    if final_mask.getbbox() is None:
        raise gr.Error("最终掩码为空，请检查掩码或 ROI。")

    return image, final_mask


def preview_mask_image(
    editor_value,
    target_text: str,
    roi_text: str,
    expand: int,
    feather: int,
    threshold: int,
    ocr_min_score: float,
    ocr_box_padding: int,
    text_match_mode: str,
):
    _, final_mask = build_final_mask(
        editor_value=editor_value,
        target_text=target_text,
        roi_text=roi_text,
        expand=expand,
        feather=feather,
        threshold=threshold,
        ocr_min_score=ocr_min_score,
        ocr_box_padding=ocr_box_padding,
        text_match_mode=text_match_mode,
    )
    return final_mask


def run_app(
    editor_value,
    target_text: str,
    roi_text: str,
    expand: int,
    feather: int,
    threshold: int,
    ocr_min_score: float,
    ocr_box_padding: int,
    text_match_mode: str,
):
    image, final_mask = build_final_mask(
        editor_value=editor_value,
        target_text=target_text,
        roi_text=roi_text,
        expand=expand,
        feather=feather,
        threshold=threshold,
        ocr_min_score=ocr_min_score,
        ocr_box_padding=ocr_box_padding,
        text_match_mode=text_match_mode,
    )

    try:
        result = run_lama_inpainting(image, final_mask)
    except RuntimeError as exc:
        raise gr.Error(str(exc)) from exc
    return final_mask, result


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="AI 去水印 LaMa") as demo:
        gr.Markdown(
            """
            # AI 去水印（LaMa）
            上传原图后，直接在图上刷白色区域作为掩码。
            白色表示要修复，透明区域表示保留。
            也可以直接输入要删除的水印文字，自动通过 OCR 找框后再交给 AI 修复。
            """
        )

        editor = gr.ImageEditor(
            type="pil",
            image_mode="RGBA",
            sources=("upload",),
            transforms=(),
            brush=gr.Brush(colors=["#ffffff"], color_mode="fixed", default_size=24),
            eraser=gr.Eraser(default_size=24),
            label="上传原图后，直接涂白色掩码",
        )

        target_text = gr.Textbox(
            label="目标水印文字（可选）",
            placeholder="例如：豆包AI生成；多个目标可用分号分隔",
        )
        roi_text = gr.Textbox(
            label="ROI（可选）",
            placeholder="例如：2350,1300,320,130；多个 ROI 用分号隔开",
        )
        with gr.Row():
            expand = gr.Slider(0, 64, value=12, step=1, label="掩码外扩")
            feather = gr.Slider(0, 32, value=6, step=1, label="边缘放宽")
            threshold = gr.Slider(0, 255, value=32, step=1, label="二值阈值")
            ocr_min_score = gr.Slider(0.0, 1.0, value=0.5, step=0.05, label="OCR 最低置信度")
            ocr_box_padding = gr.Slider(0, 48, value=10, step=1, label="OCR 外框补边")
        text_match_mode = gr.Dropdown(
            choices=["contains", "exact"],
            value="contains",
            label="文本匹配方式",
        )

        with gr.Row():
            preview_button = gr.Button("预览最终掩码")
            run_button = gr.Button("开始修复", variant="primary")

        with gr.Row():
            preview_mask_image_output = gr.Image(type="pil", label="最终掩码预览")
            output_image = gr.Image(type="pil", label="AI 修复结果")

        preview_button.click(
            fn=preview_mask_image,
            inputs=[editor, target_text, roi_text, expand, feather, threshold, ocr_min_score, ocr_box_padding, text_match_mode],
            outputs=[preview_mask_image_output],
        )
        run_button.click(
            fn=run_app,
            inputs=[editor, target_text, roi_text, expand, feather, threshold, ocr_min_score, ocr_box_padding, text_match_mode],
            outputs=[preview_mask_image_output, output_image],
        )

    return demo


if __name__ == "__main__":
    server_port = int(os.environ.get("GRADIO_SERVER_PORT", "7860"))
    _, local_url, _ = build_demo().launch(
        server_name="127.0.0.1",
        server_port=server_port,
        show_error=True,
        quiet=True,
        prevent_thread_lock=True,
    )
    print(f"AI 去水印网页已启动: {local_url}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
