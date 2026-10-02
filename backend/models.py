from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
from uuid import uuid4
from .font_manager import canonical_family


def uid():
    return uuid4().hex[:16]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class TextRegion(Model):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def fits(self):
        if self.x + self.w > 1.000001 or self.y + self.h > 1.000001:
            raise ValueError("Vùng chữ vượt khung hình nguồn")
        return self


class Scene(Model):
    id: str = Field(default_factory=uid)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    thumbnail: str = ""
    tags: str = ""
    quality: float = 0
    fingerprint: str = ""
    ai_labeled: bool = False
    text_regions: list[TextRegion] = Field(default_factory=list, max_length=12)
    text_analyzed: bool = False
    face_status: Literal["unknown", "present", "clear"] = "unknown"
    face_scan: str = ""

    @model_validator(mode="after")
    def valid_range(self):
        if self.end <= self.start:
            raise ValueError("Scene end must follow start")
        return self


class Asset(Model):
    id: str = Field(default_factory=uid)
    name: str
    filename: str
    role: Literal["source", "reference", "voice", "music", "overlay"] = "source"
    media: Literal["video", "audio", "image"]
    duration: float = 0
    width: int = 0
    height: int = 0
    fps: float = 0
    has_audio: bool = False
    thumbnail: str = ""
    tags: str = ""
    deleted: bool = False
    scenes: list[Scene] = Field(default_factory=list)


class Layer(Model):
    id: str = Field(default_factory=uid)
    kind: Literal["text", "rect", "circle", "image"] = "text"
    x: float = Field(default=0.07, ge=0, le=1)
    y: float = Field(default=0.08, ge=0, le=1)
    w: float = Field(default=0.86, gt=0, le=1)
    h: float = Field(default=0.12, gt=0, le=1)
    text: str = Field(default="{title}", max_length=500)
    content: Literal["static", "product_name", "product_description", "cta"] = "static"
    size: int = Field(default=54, ge=12, le=200)
    font_family: str = Field(
        default="Arial", min_length=1, max_length=80, pattern=r"^[\w .-]+$"
    )
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    background: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    opacity: float = Field(default=1, ge=0, le=1)
    asset_id: str = ""
    animation: Literal["none", "fade", "slide"] = "none"

    @field_validator("font_family")
    @classmethod
    def real_font_family(cls, value):
        return canonical_family(value)


class Viewport(Model):
    x: float = Field(default=0, ge=0, le=0.95)
    y: float = Field(default=0, ge=0, le=0.95)
    w: float = Field(default=1, gt=0.05, le=1)
    h: float = Field(default=1, gt=0.05, le=1)

    @model_validator(mode="after")
    def fits(self):
        if self.x + self.w > 1.001 or self.y + self.h > 1.001:
            raise ValueError("Khung hình vượt ra ngoài video")
        return self


class CaptionStyle(Model):
    enabled: bool = True
    font_size: int = Field(default=52, ge=18, le=120)
    font_family: str = Field(
        default="Arial", min_length=1, max_length=80, pattern=r"^[\w .-]+$"
    )
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    highlight: str = Field(default="#b5f36d", pattern=r"^#[0-9a-fA-F]{6}$")
    bottom: float = Field(default=0.18, ge=0.02, le=0.8)
    safe_area: Literal["standard", "reels", "cinematic"] = "standard"
    words_per_line: int = Field(default=6, ge=2, le=12)
    karaoke: bool = False

    @field_validator("font_family")
    @classmethod
    def real_font_family(cls, value):
        return canonical_family(value)


class Template(Model):
    id: str = Field(default_factory=uid)
    name: str = "Toàn khung"
    background: str = Field(default="#111111", pattern=r"^#[0-9a-fA-F]{6}$")
    viewport: Viewport = Field(default_factory=Viewport)
    layers: list[Layer] = Field(default_factory=list, max_length=20)
    slot_durations: list[float] = Field(default_factory=list, max_length=100)
    slot_layers: list[list[Layer]] = Field(default_factory=list, max_length=100)
    caption: CaptionStyle = Field(default_factory=CaptionStyle)
    transition: Literal["cut", "fade", "crossfade"] = "cut"
    transition_duration: float = Field(default=0.4, ge=0.08, le=1.5)
    notes: str = ""


class Cue(Model):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(max_length=1000)

    @model_validator(mode="after")
    def order(self):
        if self.end <= self.start:
            raise ValueError("Mốc kết thúc phải lớn hơn mốc bắt đầu")
        return self


class Clip(Model):
    id: str = Field(default_factory=uid)
    asset_id: str
    scene_id: str = ""
    locked: bool = False
    source_start: float = Field(default=0, ge=0)
    duration: float = Field(default=2.5, ge=0.2, le=180)
    speed: float = Field(default=1, ge=0.25, le=4)
    fit: Literal["cover", "contain"] = "cover"
    crop_x: float = Field(default=0.5, ge=0, le=1)
    crop_y: float = Field(default=0.5, ge=0, le=1)
    title: str = Field(default="", max_length=500)
    caption: str = Field(default="", max_length=1000)
    transition: Literal["cut", "fade", "crossfade"] = "cut"
    transition_duration: float = Field(default=0.4, ge=0.08, le=1.5)
    layers: list[Layer] = Field(default_factory=list, max_length=20)
    text_regions: list[TextRegion] = Field(default_factory=list, max_length=12)
    text_mode: Literal["inherit", "off", "cover", "blur"] = "inherit"
    text_regions_override: bool = False


class SoundEffect(Model):
    id: str = Field(default_factory=uid)
    effect: Literal[
        "whoosh",
        "pop",
        "click",
        "ding",
        "sparkle",
        "impact",
        "rise",
        "swish",
        "success",
        "camera",
    ]
    time: float = Field(ge=0, le=180)
    volume: float = Field(default=0.7, ge=0, le=1)
    origin: Literal["manual", "auto"] = "manual"


class ProductInfo(Model):
    source_id: str = ""
    name: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=1000)


class Project(Model):
    schema_version: int = 5
    id: str = Field(default_factory=uid)
    revision: int = 0
    name: str = Field(default="Video mới", min_length=1, max_length=150)
    mode: Literal["remix", "template"] = "remix"
    script: str = Field(default="", max_length=12000)
    product_name: str = Field(default="", max_length=500)
    product_description: str = Field(default="", max_length=1000)
    product_cta: str = Field(default="KHÁM PHÁ SẢN PHẨM", max_length=500)
    product_info: ProductInfo = Field(default_factory=ProductInfo)
    target_duration: float = Field(default=36, ge=5, le=180)
    aspect: Literal["9:16", "16:9", "1:1"] = "9:16"
    resolution: Literal["720", "1080"] = "1080"
    assets: list[Asset] = Field(default_factory=list)
    clips: list[Clip] = Field(default_factory=list, max_length=150)
    template: Template = Field(default_factory=Template)
    cues: list[Cue] = Field(default_factory=list, max_length=1000)
    cue_timing: str = "estimated"
    cues_edited: bool = False
    cues_stale: bool = False
    voice_id: str = ""
    music_id: str = ""
    music_volume: float = Field(default=0.15, ge=0, le=1)
    voice_volume: float = Field(default=1, ge=0, le=2)
    source_volume: float = Field(default=0, ge=0, le=1)
    ducking: bool = True
    auto_sound_effects: bool = True
    sound_effect_volume: float = Field(default=0.35, ge=0, le=1)
    sound_effects: list[SoundEffect] = Field(default_factory=list, max_length=200)
    auto_audio_assembly: bool = True
    smooth_transitions: bool = False
    avoid_faces: bool = False
    source_text_mode: Literal["off", "cover", "blur"] = "cover"
    warnings: list[str] = Field(default_factory=list)
    exports: list[dict] = Field(default_factory=list)
    updated_at: str = ""


class AIProfile(Model):
    id: str = Field(default_factory=uid, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    provider: Literal["openai", "gemini", "compatible"] = "openai"
    api_key: str = ""
    model: str = Field(min_length=1, max_length=120)
    base_url: str = ""
    enabled: bool = True


class Settings(Model):
    ai_base_url: str = "https://api.openai.com/v1"
    ai_key: str = ""
    ai_model: str = "gpt-4o-mini"
    ai_profiles: list[AIProfile] = Field(default_factory=list, max_length=20)
    active_ai_profile_id: str = ""
    ai_auto_fallback: bool = True
    tts_provider: Literal["openai", "azure", "windows"] = "openai"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "coral"
    tts_speed: float = Field(default=1, ge=0.5, le=2)
    azure_key: str = ""
    azure_region: str = "southeastasia"
    azure_voice: str = "vi-VN-HoaiMyNeural"
    windows_voice: str = ""
    transcription_provider: Literal["auto", "openai", "gemini", "local"] = "auto"
    transcription_model: str = "whisper-1"
    local_whisper_model: str = "base"
    language: str = "vi"

    @model_validator(mode="after")
    def valid_ai_profiles(self):
        ids = [profile.id for profile in self.ai_profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("ID cấu hình API bị trùng")
        if self.active_ai_profile_id and self.active_ai_profile_id not in ids:
            raise ValueError("Cấu hình API ưu tiên không tồn tại")
        return self


class Action(Model):
    use_ai: bool = False
    asset_id: str = ""
    prompt: str = Field(default="", max_length=3000)
    preset: str = "clean"
    preview: bool = False
    revision: int | None = Field(default=None, ge=0)


class AIClipChange(Model):
    id: str = Field(min_length=1, max_length=64)
    title: str | None = Field(default=None, max_length=500)
    caption: str | None = Field(default=None, max_length=1000)
    asset_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_start: float | None = Field(default=None, ge=0)
    duration: float | None = Field(default=None, ge=0.2, le=180)


class AIProposal(Model):
    message: str = Field(min_length=1, max_length=1200)
    script: str | None = Field(default=None, max_length=12000)
    music_volume: float | None = Field(default=None, ge=0, le=1)
    clip_changes: list[AIClipChange] = Field(max_length=150)


class AIApplyRequest(Model):
    revision: int = Field(ge=0)
    script: str | None = Field(default=None, max_length=12000)
    music_volume: float | None = Field(default=None, ge=0, le=1)
    clip_changes: list[AIClipChange] = Field(default_factory=list, max_length=150)


class AIApplyCommit(AIApplyRequest):
    preview_token: str = Field(min_length=20, max_length=128)


class SceneLabels(Model):
    scenes: list[dict] = Field(max_length=6)

    @model_validator(mode="after")
    def validate_entries(self):
        indexes = []
        for index, entry in enumerate(self.scenes):
            if not {"index", "tags"} <= set(entry) or set(entry) - {
                "index",
                "tags",
                "text_regions",
            }:
                raise ValueError(
                    f"scenes[{index}] must contain index, tags and optional text_regions"
                )
            if not isinstance(entry["index"], int) or isinstance(entry["index"], bool):
                raise ValueError(f"scenes[{index}].index must be an integer")
            if not isinstance(entry["tags"], str) or not entry["tags"].strip():
                raise ValueError(f"scenes[{index}].tags must be non-empty text")
            if "text_regions" in entry:
                if (
                    not isinstance(entry["text_regions"], list)
                    or len(entry["text_regions"]) > 12
                ):
                    raise ValueError("text_regions phải là danh sách tối đa 12 vùng")
                for region in entry["text_regions"]:
                    TextRegion.model_validate(region)
            indexes.append(entry["index"])
        if len(indexes) != len(set(indexes)):
            raise ValueError("scene label indexes must be unique")
        return self
