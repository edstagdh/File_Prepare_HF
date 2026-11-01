"""
Build UI form descriptions from the pydantic settings models, mask secrets and merge submitted values.

The browser renders every form generically from these descriptions (see static/app.js ``renderForm``),
so new settings fields appear in the UI without template changes.
"""

import types
import typing
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from fileprep.config.help import HELP
from fileprep.config.models import (Credentials, JobConfig, ProcessingSettings, ThumbnailSettings, TrackerConfig,
                                    VideoPreviewSettings)

PROCESSING_SECTIONS = set(ProcessingSettings.model_fields)
TRACKER_SECTIONS = {"browser", "torrent", "sites", "form"}


def _extra(field_info) -> dict:
    extra = field_info.json_schema_extra
    return dict(extra) if isinstance(extra, dict) else {}


def _resolve_visible(expr: str | None, root: str) -> str | None:
    """Turn a section-relative rule ('outputs.cover_image') into a JobConfig path ('processing.outputs.cover_image')."""
    if not expr:
        return None
    if "||" in expr:  # any of several rules
        return " || ".join(_resolve_visible(part.strip(), root) for part in expr.split("||"))
    head = expr.split(".", 1)[0].split()[0]
    if head in PROCESSING_SECTIONS:
        return "processing." + expr
    if head in TRACKER_SECTIONS:
        return "tracker." + expr
    return f"{root}.{expr}" if root else expr


def _choices_from(source: str) -> list[str]:
    if source == "tracker_sites":
        from fileprep.config.store import store
        return list(store.load("tracker").sites)
    return []


def describe_field(name: str, field_info, path: str, root: str, owner: type | None = None) -> dict:
    extra = _extra(field_info)
    help_text = field_info.description or (HELP.get(f"{owner.__name__}.{name}", "") if owner else "")
    annotation = field_info.annotation
    origin = get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        annotation = next(a for a in get_args(annotation) if a is not type(None))
        origin = get_origin(annotation)
    desc: dict[str, Any] = {"key": path, "name": name, "label": extra.get("label") or name.replace("_", " ").capitalize(),
                            "help": help_text, "visible_if": _resolve_visible(extra.get("visible_if"), root),
                            "secret": bool(extra.get("secret")), "hidden": bool(extra.get("hidden")),
                            "advanced": bool(extra.get("advanced"))}
    for key in ("min", "max", "step", "choices_help", "multiline"):
        if key in extra:
            desc[key] = extra[key]
    if annotation is bool:
        desc["type"] = "bool"
    elif annotation is int:
        desc["type"] = "int"
    elif annotation is float:
        desc["type"] = "float"
    elif annotation is str:
        desc["type"] = "secret" if desc["secret"] else "str"
    elif origin is Literal:
        desc["type"], desc["choices"] = "select", [str(c) for c in get_args(annotation)]
        desc["value_type"] = "int" if all(isinstance(c, int) for c in get_args(annotation)) else "str"
    elif origin is list:
        item = get_args(annotation)[0]
        if get_origin(item) is Literal or extra.get("choices_from"):
            choices = _choices_from(extra["choices_from"]) if extra.get("choices_from") else [str(c) for c in get_args(item)]
            desc["type"], desc["choices"] = ("ordered" if extra.get("ordered") else "multiselect"), choices
            desc["choices_from"] = extra.get("choices_from")
        elif item is int:
            desc["type"] = "intlist"
        elif item is float:
            desc["type"] = "floatlist"
        elif isinstance(item, type) and issubclass(item, BaseModel):
            desc["type"] = "json"
        else:
            desc["type"] = "list"
    elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
        desc["type"] = "model"
        desc["fields"] = [describe_field(n, f, f"{path}.{n}", root, annotation) for n, f in annotation.model_fields.items()]
    elif origin is dict:
        value_type = get_args(annotation)[1]
        if isinstance(value_type, type) and issubclass(value_type, BaseModel):
            desc["type"] = "dict_models"
            desc["addable"], desc["key_label"] = bool(extra.get("addable")), extra.get("key_label", "name")
            desc["item_defaults"] = value_type().model_dump(mode="json") if extra.get("addable") else None
            desc["item_fields"] = [describe_field(n, f, n, root, value_type) for n, f in value_type.model_fields.items()]
            for item in desc["item_fields"]:
                if item["name"] in ("password",):
                    item["type"], item["secret"] = "secret", True
        else:
            desc["type"] = "json"
    else:
        desc["type"] = "json"
    return desc


def describe_sections(model: type[BaseModel], root: str, visible_if: str | None = None,
                      label: str | None = None, icon: str = "") -> list[dict]:
    """One section per nested model field; a flat model becomes a single section."""
    sections, loose = [], []
    for name, field_info in model.model_fields.items():
        extra = _extra(field_info)
        annotation = field_info.annotation
        path = f"{root}.{name}" if root else name
        if extra.get("section") and isinstance(annotation, type) and issubclass(annotation, BaseModel):
            sections.append({"key": path, "label": extra.get("label", name), "icon": extra.get("icon", ""),
                             "help": field_info.description or "",
                             "visible_if": _resolve_visible(extra.get("visible_if"), root),
                             "fields": [describe_field(n, f, f"{path}.{n}", root, annotation) for n, f in annotation.model_fields.items()]})
        else:
            loose.append(describe_field(name, field_info, path, root, model))
    if loose:
        sections.append({"key": root or "general", "label": label or "Advanced", "icon": icon, "help": "",
                         "visible_if": visible_if, "fields": loose})
    return sections


def job_form() -> list[dict]:
    """Sections of the job form: processing + thumbnails/preview/tracker settings when those features are on."""
    sections = describe_sections(ProcessingSettings, "processing")
    sections += describe_sections(ThumbnailSettings, "thumbnails", "processing.outputs.thumbnails_sheet", "Thumbnails sheet", "grid")
    sections += describe_sections(VideoPreviewSettings, "video_preview", "processing.outputs.video_preview", "Video preview", "film")
    for section in describe_sections(TrackerConfig, "tracker", "processing.tracker.enabled", "Tracker sites", "globe"):
        section["visible_if"] = section["visible_if"] or "processing.tracker.enabled"
        sections.append(section)
    return sections


def settings_form(name: str, model: type[BaseModel]) -> list[dict]:
    labels = {"thumbnails": ("Thumbnails sheet", "grid"), "video_preview": ("Video preview", "film"),
              "tracker": ("Tracker sites", "globe")}
    label, icon = labels.get(name, ("General", ""))
    sections = describe_sections(model, "", None, label, icon)
    for section in sections:  # settings pages always show every section
        section["visible_if"] = None
        _strip_section_rules(section, name)
    return sections


def _strip_section_rules(section: dict, name: str) -> None:
    # rules that point outside this settings file cannot be evaluated on its own page
    for f in section["fields"]:
        parts = [_strip_rule(part.strip(), name) for part in (f.get("visible_if") or "").split("||")]
        f["visible_if"] = None if not all(parts) else " || ".join(parts)


def _strip_rule(rule: str, name: str) -> str | None:
    if rule.startswith("processing.") and name != "processing":
        return None
    if rule.startswith("processing."):
        return rule[len("processing."):]
    if rule.startswith("tracker.") and name == "tracker":
        return rule[len("tracker."):]
    return rule


# ------------------------------------------------------------------ secrets
def _secret_paths(model: type[BaseModel], prefix: str = "") -> list[str]:
    paths = []
    for name, field_info in model.model_fields.items():
        annotation = field_info.annotation
        if _extra(field_info).get("secret"):
            paths.append(prefix + name)
        elif isinstance(annotation, type) and issubclass(annotation, BaseModel):
            paths += _secret_paths(annotation, prefix + name + ".")
    return paths


def mask_secrets(model: BaseModel) -> tuple[dict, dict[str, bool]]:
    """Dump ``model`` with secret values blanked. Returns (data, {path: has_value})."""
    data = model.model_dump(mode="json")
    flags = {}
    for path in _secret_paths(type(model)):
        node = data
        *parents, leaf = path.split(".")
        for part in parents:
            node = node[part]
        flags[path] = bool(node.get(leaf))
        node[leaf] = ""
    if isinstance(model, Credentials):
        for name, account in data.get("trackers", {}).items():
            flags[f"trackers.{name}.password"] = bool(account.get("password"))
            account["password"] = ""
    return data, flags


def merge_secrets(new: dict, current: BaseModel) -> dict:
    """Empty secret fields in a submitted form keep their current value."""
    existing = current.model_dump(mode="json")
    for path in _secret_paths(type(current)):
        *parents, leaf = path.split(".")
        node_new, node_old = new, existing
        try:
            for part in parents:
                node_new, node_old = node_new[part], node_old[part]
        except (KeyError, TypeError):
            continue
        if not node_new.get(leaf):
            node_new[leaf] = node_old.get(leaf, "")
    if isinstance(current, Credentials):
        for name, account in new.get("trackers", {}).items():
            if not account.get("password"):
                account["password"] = existing.get("trackers", {}).get(name, {}).get("password", "")
    return new


def job_config_from_form(data: dict) -> JobConfig:
    return JobConfig.model_validate(data)
