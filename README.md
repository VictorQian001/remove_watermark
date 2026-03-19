[中文](./README.md) | [English](./README_EN.md)

# remove_watermark

基于开源 AI 图像补全模型 LaMa 的去水印工具。仓库已经移除旧的 OpenCV 规则方案，当前只保留 AIGC 版本。

当前流程分成两步：

- 用手工掩码、ROI 或 OCR 找到要移除的水印区域
- 用 LaMa 对目标区域做局部 AI 补全

这套方案对草地、墙面、布料、重复纹理、渐变背景这类传统修补容易露馅的场景更稳，也更适合按“指定文字水印”做批量处理。

## 对比图

`avatar1`：

![avatar1 before after](./assets/avatar1_compare.png)

`garden`：

![garden before after](./assets/garden_compare.png)

## 功能

- CLI 单图去水印
- 目录批处理
- 输入目标水印文字，自动 OCR 找框
- 导出 OCR 检测框预览图，便于批量验框
- 本地网页涂抹掩码
- ROI、手工掩码、OCR 文字匹配三种方式可混用

## 依赖

- 建议 Python `3.11`
- `simple-lama-inpainting` 当前依赖 `Pillow<10`
- 建议优先使用带 GPU 的环境；CPU 也能跑，但首次推理更慢

安装：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -r requirements.txt
```

如果你已经像当前仓库一样准备了一个本地 conda 环境，也可以直接用对应环境里的 Python。

## 项目结构

```text
remove_watermark/
├── app.py
├── cli.py
├── inpaint_core.py
├── requirements.txt
├── README.md
├── README_EN.md
└── assets/
    ├── avatar1_compare.png
    ├── avatar1.png
    ├── garden_compare.png
    └── garden.png
```

## CLI

1. 用外部掩码图：

```bash
python cli.py \
  -i ./assets/avatar1.png \
  -o ./outputs/avatar1_clean.png \
  --mask ./your_mask.png \
  --mask-output ./outputs/avatar1_mask.png
```

2. 用 ROI：

```bash
python cli.py \
  -i ./assets/garden.png \
  -o ./outputs/garden_clean.png \
  --roi 2350,1300,320,130 \
  --mask-output ./outputs/garden_mask.png
```

3. 直接输入要移除的文字：

```bash
python cli.py \
  -i ./assets/avatar1.png \
  -o ./outputs/avatar1_clean.png \
  --target-text "豆包AI生成" \
  --ocr-preview ./outputs/avatar1_ocr_preview.png \
  --mask-output ./outputs/avatar1_mask.png
```

4. 目录批处理：

```bash
python cli.py \
  -i ./batch_inputs \
  -o ./batch_outputs \
  --target-text "豆包AI生成" \
  --ocr-preview ./batch_ocr_preview \
  --mask-output ./batch_masks
```

说明：

- 目录模式下，`--mask-output` 表示掩码输出目录
- 目录模式下，`--ocr-preview` 表示 OCR 预览图输出目录
- 当前目录模式支持 `png/jpg/jpeg/webp/bmp`
- 目录模式下不支持单个 `--mask` 文件
- 某张图没有匹配到目标文字时，会打印 `[WARN]` 并继续处理其他图片

## 常用参数

- `--mask`：外部掩码图，白色区域会被修复
- `--roi`：矩形区域，格式 `x,y,w,h`
- `--target-text`：要去除的文字，先 OCR 找框再修复
- `--text-match-mode`：`contains` 或 `exact`
- `--ocr-min-score`：OCR 最低置信度，默认 `0.5`
- `--ocr-box-padding`：OCR 命中文字框外围额外补的像素，适合去掉文字外发光/灰边
- `--flat-bg-mode`：纯色背景模式，对修复区域附近做局部颜色平滑
- `--flat-bg-blur`：纯色背景模式下的平滑半径，默认 `24`
- `--flat-bg-strength`：纯色背景模式下的平滑强度，默认 `0.8`
- `--ocr-preview`：导出 OCR 检测框预览图；匹配框为红色，其他 OCR 框为蓝色
- `--mask-output`：保存最终送入 AI 的掩码
- `--expand`：掩码外扩像素，默认 `12`
- `--feather`：对掩码边缘做轻微放宽，默认 `6`
- `--mask-threshold`：掩码二值化阈值，默认 `32`
- `--invert-mask`：如果外部掩码黑白语义相反可打开

## Web UI

启动：

```bash
python app.py
```

网页里可以：

- 上传原图
- 输入目标水印文字，让 OCR 自动找框
- 直接在图上刷白色掩码
- 补充填写 ROI
- 开启“纯色背景模式”继续压浅灰残边
- 先预览最终掩码，再执行 AI 修复

## 调参建议

如果是纯色或接近纯色的背景，去掉文字后还残留一圈灰边，通常不是 LaMa 本身的问题，而是水印外面的半透明发光/阴影没有被掩码盖住。优先这样调：

- 先增大 `--ocr-box-padding`
- 再增大 `--expand`
- 还不够的话，再打开 `--flat-bg-mode`
- 最后再微调 `--flat-bg-blur` 和 `--flat-bg-strength`

针对类似 `豆包AI生成` 这种白字 + 灰色外发光，建议先试：

```bash
python cli.py \
  -i ./assets/avatar1.png \
  -o ./outputs/avatar1_clean.png \
  --target-text "豆包AI生成" \
  --ocr-box-padding 16 \
  --expand 14 \
  --feather 4 \
  --flat-bg-mode \
  --flat-bg-blur 24 \
  --flat-bg-strength 0.8
```

## 示例素材

- [avatar1.png](./assets/avatar1.png)
- [garden.png](./assets/garden.png)

## 适用边界

更适合：

- 角落 Logo 或角落文字
- 半透明或浅色文字水印
- 背景纹理较复杂的局部去除
- 批量处理同一类文字水印

仍然不适合：

- 大面积覆盖整张图的重度水印
- 关键主体被完全遮挡的情况
- 需要严格还原真实细节的司法或鉴定场景

## 合规使用

- 仅处理你本人拥有、授权使用或明确有权编辑的图片
- 请确保使用场景符合图片来源平台条款和适用法律法规
