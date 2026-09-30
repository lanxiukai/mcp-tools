"""Small parameter contracts expand into ordinary, fully editable diagram specs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .models import Diagram, Model
from .renderer import render
from .artifacts import Detail, Preview

TemplateName = Literal[
    "residual",
    "encoder-decoder",
    "attention",
    "loss-branches",
    "tensor-stack",
    "cvae",
    "stylegan2",
    "vq-vae",
]
TemplateLayout = Literal["grid", "layered", "elk"]


class Common(Model):
    language: Literal["en", "zh"] = "en"
    title: str | None = Field(default=None, max_length=240)


class Residual(Common):
    channels: int = Field(default=64, ge=1, le=16384)
    layers: int = Field(default=2, ge=1, le=8)
    kernel_size: int = Field(default=3, ge=1, le=15)


class EncoderDecoder(Common):
    channels: list[int] = Field(
        default_factory=lambda: [64, 128, 256], min_length=1, max_length=6
    )
    latent_dim: int = Field(default=128, ge=1, le=16384)
    skip_connections: bool = False

    @model_validator(mode="after")
    def channel_range(self):
        if any(not 1 <= value <= 16384 for value in self.channels):
            raise ValueError("Channel counts must be in [1, 16384]")
        return self


class Attention(Common):
    model_dim: int = Field(default=512, ge=1, le=16384)
    heads: int = Field(default=8, ge=1, le=128)
    tokens: int = Field(default=128, ge=1, le=1048576)

    @model_validator(mode="after")
    def equal_heads(self):
        if self.model_dim % self.heads:
            raise ValueError("model_dim must be divisible by heads")
        return self


class LossTerm(Model):
    label: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=128)
    formula: str | None = Field(default=None, min_length=1, max_length=1024)


class LossBranches(Common):
    terms: list[LossTerm] | None = Field(default=None, min_length=2, max_length=6)


class TensorStack(Common):
    shapes: list[list[int]] = Field(
        default_factory=lambda: [[3, 64, 64], [64, 32, 32], [128, 16, 16]],
        min_length=1,
        max_length=6,
    )
    operations: list[str] | None = Field(default=None, max_length=5)

    @model_validator(mode="after")
    def shape_contract(self):
        if any(
            not 1 <= len(shape) <= 5 or any(not 1 <= n <= 1048576 for n in shape)
            for shape in self.shapes
        ):
            raise ValueError(
                "Each tensor shape needs 1-5 positive dimensions up to 1048576"
            )
        if self.operations is not None and (
            len(self.operations) != len(self.shapes) - 1
            or any(not 1 <= len(s) <= 120 for s in self.operations)
        ):
            raise ValueError("Provide one operation name per adjacent pair of tensors")
        return self


PARAMETERS = {
    "residual": Residual,
    "encoder-decoder": EncoderDecoder,
    "attention": Attention,
    "loss-branches": LossBranches,
    "tensor-stack": TensorStack,
    "cvae": Common,
    "stylegan2": Common,
    "vq-vae": Common,
}
TITLES = {
    "residual": ("Residual block", "残差块"),
    "encoder-decoder": ("Encoder and decoder", "编码器与解码器"),
    "attention": ("Multi-head attention", "多头注意力"),
    "loss-branches": ("Loss aggregation", "多分支损失汇总"),
    "tensor-stack": ("Tensor transformations", "张量形状变换"),
    "cvae": ("Conditional VAE", "cVAE · 条件嵌入与两处拼接"),
    "stylegan2": ("StyleGAN2 feature and RGB paths", "StyleGAN2 · 特征更新与 RGB 累加"),
    "vq-vae": ("VQ-VAE tokenizer and losses", "VQ-VAE · 前向计算与三项损失"),
}
# Localization is intentionally limited to generated diagram labels.
ZH = {
    "Input": "输入",
    "Output": "输出",
    "Conv": "卷积",
    "Activation": "激活",
    "Encoder": "编码器",
    "Decoder": "解码器",
    "Latent": "潜变量",
    "Projection": "线性投影",
    "Attention scores": "注意力分数",
    "Weighted values": "加权求和",
    "Concat + project": "拼接与投影",
    "Reconstruction": "重建",
    "Codebook": "码本",
    "Commitment": "承诺",
    "Total objective": "总目标",
    "Tensor": "张量",
    "Transform": "变换",
    "Condition": "条件",
    "Embedding": "条件嵌入",
    "Features": "图像特征",
    "Concat": "拼接",
    "Posterior": "近似后验",
    "Sample": "采样",
    "Parameters": "观测参数",
    "Prior": "条件先验",
    "Rate": "信息率",
    "Distortion": "失真",
    "Objective": "总目标",
    "Hidden input": "隐藏特征输入",
    "Up-StyledConv": "上采样风格卷积",
    "StyledConv": "风格卷积",
    "Hidden output": "隐藏特征输出",
    "Previous RGB": "上一尺度 RGB",
    "Upsample": "RGB 上采样",
    "Updated RGB": "当前尺度 RGB",
    "Hidden feature path": "隐藏特征路径",
    "RGB accumulation path": "RGB 累加路径",
    "Image": "图像",
    "Nearest + lookup": "最近邻与查表",
    "Reconstruction loss": "重建损失",
    "Codebook loss": "码本损失",
    "Commitment loss": "承诺损失",
}


def catalog(template: TemplateName | None = None, include_schema: bool = False) -> dict:
    if template is None:
        return {
            "templates": [
                {"name": name, "description": titles[0]}
                for name, titles in TITLES.items()
            ]
        }
    model = PARAMETERS[template]
    result = {
        "template": template,
        "description": TITLES[template][0],
        "defaults": model().model_dump(exclude_none=True),
    }
    if include_schema:
        result["parameter_schema"] = model.model_json_schema()
    return result


def build(
    template: TemplateName,
    parameters: dict | None = None,
    theme: str = "dark",
    layout: TemplateLayout = "grid",
) -> Diagram:
    if template not in PARAMETERS:
        raise ValueError(f"Unknown template: {template}")
    params = PARAMETERS[template].model_validate(parameters or {})
    zh = params.language == "zh"

    def word(text):
        return ZH.get(text, text) if zh else text

    nodes, edges = [], []

    def node(id, title, row, column, latex=None, role="default", **options):
        labels = [{"text": word(title), "font_size": 19, "weight": 700}]
        if latex:
            labels.append({"latex": latex, "font_size": 19})
        nodes.append(
            {
                "id": id,
                "labels": labels,
                "row": row,
                "column": column,
                "role": role,
                **options,
            }
        )
        return id

    def edge(source, target, **options):
        edges.append(
            {
                "id": f"{source}-to-{target}",
                "source": source,
                "target": target,
                **options,
            }
        )

    if template in {"cvae", "stylegan2", "vq-vae"}:
        path = Path(__file__).resolve().parents[1] / "examples" / f"{template}.json"
        spec = json.loads(path.read_text())
        for item in spec["nodes"]:
            for label in item["labels"]:
                if "text" in label:
                    label["text"] = word(label["text"])
        for group in spec.get("groups", []):
            group["title"] = word(group["title"])
    elif isinstance(params, Residual):
        node("input", "Input", 0, 0, "x", "state")
        previous = "input"
        for i in range(params.layers):
            name = node(
                f"conv-{i + 1}",
                "Conv",
                0,
                i + 1,
                f"{params.kernel_size}\\times{params.kernel_size},\\ C={params.channels}",
                "feature",
            )
            edge(previous, name, role="feature")
            previous = name
        node(
            "sum",
            "+",
            0,
            params.layers + 1,
            role="state",
            shape="circle",
            width=64,
            height=64,
        )
        node("output", "Output", 0, params.layers + 2, "y=F(x)+x", "state")
        edge(previous, "sum", role="feature")
        edge(
            "input",
            "sum",
            source_port="bottom",
            target_port="bottom",
            role="state",
            dashed=True,
        )
        edge("sum", "output", role="state")
    elif isinstance(params, EncoderDecoder):
        depth = len(params.channels)
        node("input", "Input", 0, 0, "x", "state")
        previous = "input"
        for i, channels in enumerate(params.channels):
            name = node(
                f"encoder-{i + 1}", "Encoder", 0, i + 1, f"C={channels}", "feature"
            )
            edge(previous, name, role="feature")
            previous = name
        node(
            "latent",
            "Latent",
            0,
            depth + 1,
            rf"z\in\mathbb{{R}}^{{{params.latent_dim}}}",
            "condition",
        )
        edge(previous, "latent")
        previous = "latent"
        for i, channels in reversed(list(enumerate(params.channels))):
            name = node(
                f"decoder-{i + 1}",
                "Decoder",
                0,
                2 * depth - i + 1,
                f"C={channels}",
                "feature",
            )
            edge(previous, name, role="feature")
            if params.skip_connections:
                edge(
                    f"encoder-{i + 1}",
                    name,
                    source_port="bottom",
                    target_port="bottom",
                    dashed=True,
                    role="condition",
                )
            previous = name
        node("output", "Output", 0, 2 * depth + 2, r"\hat{x}", "state")
        edge(previous, "output", role="state")
    elif isinstance(params, Attention):
        per_head = params.model_dim // params.heads
        node(
            "input",
            "Input",
            1,
            0,
            rf"X\in\mathbb{{R}}^{{{params.tokens}\times{params.model_dim}}}",
            "state",
        )
        for row, symbol in enumerate("QKV"):
            name = node(
                symbol.lower(),
                f"{symbol} projection" if not zh else symbol + " 投影",
                row,
                1,
                rf"{symbol}:\ {params.heads}\times{params.tokens}\times{per_head}",
                "feature",
            )
            edge(
                "input",
                name,
                source_port=["top", "right", "bottom"][row],
                target_port="left",
                role="feature",
            )
        node(
            "scores",
            "Attention scores",
            0,
            2,
            r"QK^{\mathsf{T}}/\sqrt{d_k}",
            "condition",
        )
        node("softmax", "Softmax", 0, 3, r"A=\operatorname{softmax}(S)", "condition")
        node("weighted", "Weighted values", 1, 4, "AV", "feature")
        node(
            "project",
            "Concat + project",
            1,
            5,
            r"\operatorname{Concat}(H_1,\ldots,H_h)W_O",
            "feature",
        )
        node(
            "output",
            "Output",
            1,
            6,
            rf"Y\in\mathbb{{R}}^{{{params.tokens}\times{params.model_dim}}}",
            "state",
        )
        edge("q", "scores", role="feature")
        edge("k", "scores", source_port="right", target_port="bottom", role="feature")
        edge("scores", "softmax", role="condition")
        edge(
            "softmax",
            "weighted",
            source_port="right",
            target_port="top",
            role="condition",
        )
        edge("v", "weighted", source_port="right", target_port="bottom", role="feature")
        edge("weighted", "project", role="feature")
        edge("project", "output", role="state")
    elif isinstance(params, LossBranches):
        terms = params.terms or [
            LossTerm(label=word(name), symbol=rf"\mathcal{{L}}_{{\mathrm{{{symbol}}}}}")
            for name, symbol in [
                ("Reconstruction", "rec"),
                ("Codebook", "cb"),
                ("Commitment", "com"),
            ]
        ]
        for i, term in enumerate(terms):
            node(f"loss-{i + 1}", term.label, 0, i, term.formula or term.symbol, "loss")
        middle = (len(terms) - 1) // 2
        node(
            "total",
            "Total objective",
            1,
            middle,
            r"\mathcal{L}=" + "+".join(term.symbol for term in terms),
            "loss",
        )
        for i in range(len(terms)):
            edge(
                f"loss-{i + 1}",
                "total",
                source_port="bottom",
                target_port="left" if i < middle else "top" if i == middle else "right",
                role="loss",
            )
    elif isinstance(params, TensorStack):
        for i, dimensions in enumerate(params.shapes):
            node(
                f"tensor-{i + 1}",
                "Tensor",
                0,
                i * 2,
                r"\times".join(map(str, dimensions)),
                "state",
                shape="tensor",
            )
            if i:
                title = (
                    params.operations[i - 1] if params.operations else word("Transform")
                )
                node(f"op-{i}", title, 0, i * 2 - 1, role="feature")
                edge(f"tensor-{i}", f"op-{i}", role="feature")
                edge(f"op-{i}", f"tensor-{i + 1}", role="state")
    if template not in {"cvae", "stylegan2", "vq-vae"}:
        spec = {"nodes": nodes, "edges": edges, "layout": {"gap_x": 72, "gap_y": 90}}
    spec.update(
        title=params.title if params.title is not None else TITLES[template][int(zh)],
        theme=theme,
    )
    spec["layout"]["mode"] = layout
    if layout != "grid":
        for item in spec["nodes"]:
            for key in ("row", "column", "x", "y"):
                item.pop(key, None)
    return Diagram.model_validate(spec)


def create(
    template: TemplateName,
    output_path: str,
    parameters: dict | None = None,
    theme: str = "dark",
    layout: TemplateLayout = "grid",
    preview: Preview = "inline",
    detail: Detail = "summary",
    overwrite: bool = False,
) -> dict:
    spec = build(template, parameters, theme, layout)
    return render(spec, output_path, overwrite, preview=preview, detail=detail)
