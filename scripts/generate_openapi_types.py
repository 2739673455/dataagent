"""从后端 OpenAPI 生成前端 TypeScript 协议"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "web" / "src" / "api" / "generated.ts"
HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")
PARAMETER_LOCATIONS = ("path", "query", "header", "cookie")


def _load_openapi() -> dict[str, Any]:
    """加载当前 FastAPI 应用生成的 OpenAPI"""
    project_root = str(PROJECT_ROOT)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    module = importlib.import_module("main")
    return module.app.openapi()


def _quote(value: str) -> str:
    """生成可用于 TypeScript 字面量的 JSON 字符串"""
    return json.dumps(value, ensure_ascii=False)


def _literal(value: Any) -> str:
    """将简单 JSON 值渲染为 TypeScript 字面量"""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, int | float):
        return repr(value)
    return "unknown"


def _unique(items: Sequence[str]) -> list[str]:
    """按原有顺序移除重复类型表达式"""
    return list(dict.fromkeys(items))


def _render_ref(ref: str) -> str:
    """将 OpenAPI Schema 引用渲染为组件类型索引"""
    prefix = "#/components/schemas/"
    if not ref.startswith(prefix):
        return "unknown"
    name = ref.removeprefix(prefix).replace("~1", "/").replace("~0", "~")
    return f'components["schemas"][{_quote(name)}]'


def _render_schema(schema: Any, indent: int = 0) -> str:
    """把 OpenAPI JSON Schema 转换为 TypeScript 类型"""
    if not isinstance(schema, Mapping):
        return "unknown"
    if ref := schema.get("$ref"):
        return _render_ref(str(ref))
    if "const" in schema:
        return _literal(schema["const"])
    if enum := schema.get("enum"):
        return " | ".join(_literal(value) for value in enum)

    for keyword, separator in (("anyOf", " | "), ("oneOf", " | "), ("allOf", " & ")):
        variants = schema.get(keyword)
        if isinstance(variants, list):
            rendered = _unique([_render_schema(item, indent) for item in variants])
            return f"({separator.join(rendered)})"

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        rendered = _unique(
            [
                _render_schema({**schema, "type": item}, indent)
                for item in schema_type
            ]
        )
        return f"({' | '.join(rendered)})"
    if schema_type == "null":
        return "null"
    if schema_type == "string":
        return "string"
    if schema_type in {"integer", "number"}:
        return "number"
    if schema_type == "boolean":
        return "boolean"
    if schema_type == "array":
        return f"Array<{_render_schema(schema.get('items', {}), indent)}>"

    properties = schema.get("properties")
    additional = schema.get("additionalProperties")
    if schema_type == "object" or isinstance(properties, Mapping) or additional is not None:
        lines = ["{"]
        child_indent = "  " * (indent + 1)
        required = set(schema.get("required", []))
        if isinstance(properties, Mapping):
            for name in sorted(properties):
                optional = "" if name in required else "?"
                rendered = _render_schema(properties[name], indent + 1)
                lines.append(f"{child_indent}{_quote(name)}{optional}: {rendered};")
        if additional is True:
            lines.append(f"{child_indent}[key: string]: unknown;")
        elif isinstance(additional, Mapping):
            rendered = _render_schema(additional, indent + 1)
            lines.append(f"{child_indent}[key: string]: {rendered};")
        lines.append(f"{'  ' * indent}}}")
        return "\n".join(lines)
    return "unknown"


def _resolve(document: Mapping[str, Any], value: Any) -> Any:
    """解析 OpenAPI 文档内的本地 JSON 引用"""
    if not isinstance(value, Mapping) or "$ref" not in value:
        return value
    ref = str(value["$ref"])
    if not ref.startswith("#/"):
        return value
    current: Any = document
    for segment in ref.removeprefix("#/").split("/"):
        if not isinstance(current, Mapping):
            return value
        current = current.get(segment.replace("~1", "/").replace("~0", "~"))
    return current


def _render_parameters(
    document: Mapping[str, Any],
    parameters: Sequence[Any],
    indent: int,
) -> str:
    """按来源分组并渲染接口参数类型"""
    groups: dict[str, list[Mapping[str, Any]]] = {
        location: [] for location in PARAMETER_LOCATIONS
    }
    for raw_parameter in parameters:
        parameter = _resolve(document, raw_parameter)
        if not isinstance(parameter, Mapping):
            continue
        location = str(parameter.get("in", ""))
        if location in groups:
            groups[location].append(parameter)

    lines = ["{"]
    for location in PARAMETER_LOCATIONS:
        location_indent = "  " * (indent + 1)
        parameter_indent = "  " * (indent + 2)
        values = sorted(groups[location], key=lambda item: str(item.get("name", "")))
        if not values:
            lines.append(f'{location_indent}{_quote(location)}?: never;')
            continue
        lines.append(f'{location_indent}{_quote(location)}: {{')
        for parameter in values:
            name = str(parameter["name"])
            optional = "" if parameter.get("required") else "?"
            rendered = _render_schema(parameter.get("schema", {}), indent + 2)
            lines.append(f"{parameter_indent}{_quote(name)}{optional}: {rendered};")
        lines.append(f"{location_indent}}};")
    lines.append(f"{'  ' * indent}}}")
    return "\n".join(lines)


def _render_content(content: Any, indent: int) -> str:
    """按媒体类型渲染请求或响应内容类型"""
    if not isinstance(content, Mapping) or not content:
        return "never"
    lines = ["{"]
    child_indent = "  " * (indent + 1)
    for media_type in sorted(content):
        media = content[media_type]
        schema = media.get("schema", {}) if isinstance(media, Mapping) else {}
        lines.append(
            f"{child_indent}{_quote(str(media_type))}: "
            f"{_render_schema(schema, indent + 1)};"
        )
    lines.append(f"{'  ' * indent}}}")
    return "\n".join(lines)


def _render_operation(
    document: Mapping[str, Any],
    path_parameters: Sequence[Any],
    operation: Mapping[str, Any],
) -> str:
    """渲染一个 OpenAPI 操作的参数、请求体和响应"""
    parameters = [*path_parameters, *operation.get("parameters", [])]
    lines = ["{"]
    lines.append(
        f'    "parameters": {_render_parameters(document, parameters, 2)};'
    )

    request_body = _resolve(document, operation.get("requestBody"))
    if isinstance(request_body, Mapping):
        optional = "" if request_body.get("required") else "?"
        content = _render_content(request_body.get("content"), 2)
        lines.append(f'    "requestBody"{optional}: {content};')
    else:
        lines.append('    "requestBody"?: never;')

    lines.append('    "responses": {')
    responses = operation.get("responses", {})
    if isinstance(responses, Mapping):
        for status_code in sorted(responses):
            response = _resolve(document, responses[status_code])
            content = (
                _render_content(response.get("content"), 3)
                if isinstance(response, Mapping)
                else "never"
            )
            lines.append(f'      {_quote(str(status_code))}: {{')
            lines.append(f'        "content": {content};')
            lines.append("      };")
    lines.append("    };")
    lines.append("  }")
    return "\n".join(lines)


def _render_openapi_types(document: Mapping[str, Any]) -> str:
    """生成稳定排序的 TypeScript OpenAPI 协议"""
    components = document.get("components", {})
    schemas = components.get("schemas", {}) if isinstance(components, Mapping) else {}
    paths = document.get("paths", {})
    lines = [
        "/* Generated by scripts/generate_openapi_types.py */",
        "/* Run uv run python scripts/generate_openapi_types.py to update */",
        "",
        "export interface components {",
        '  "schemas": {',
    ]
    if isinstance(schemas, Mapping):
        for name in sorted(schemas):
            rendered = _render_schema(schemas[name], 2)
            lines.append(f"    {_quote(str(name))}: {rendered};")
    lines.extend(["  };", "}", "", "export interface operations {"])

    operations: list[tuple[str, Mapping[str, Any], Sequence[Any]]] = []
    if isinstance(paths, Mapping):
        for path in sorted(paths):
            path_item = paths[path]
            if not isinstance(path_item, Mapping):
                continue
            path_parameters = path_item.get("parameters", [])
            for method in HTTP_METHODS:
                operation = path_item.get(method)
                if isinstance(operation, Mapping):
                    operation_id = str(operation["operationId"])
                    operations.append((operation_id, operation, path_parameters))
    for operation_id, operation, path_parameters in sorted(operations):
        rendered = _render_operation(document, path_parameters, operation)
        lines.append(f"  {_quote(operation_id)}: {rendered};")
    lines.extend(["}", "", "export interface paths {"])

    if isinstance(paths, Mapping):
        for path in sorted(paths):
            path_item = paths[path]
            if not isinstance(path_item, Mapping):
                continue
            lines.append(f"  {_quote(str(path))}: {{")
            for method in HTTP_METHODS:
                operation = path_item.get(method)
                if isinstance(operation, Mapping):
                    operation_id = str(operation["operationId"])
                    lines.append(
                        f"    {_quote(method)}: operations[{_quote(operation_id)}];"
                    )
            lines.append("  };")
    lines.extend(["}", ""])
    return "\n".join(lines)


def main() -> int:
    """生成或校验前端 OpenAPI TypeScript 协议文件"""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="检查已提交类型是否与当前 OpenAPI 一致",
    )
    args = parser.parse_args()
    generated = _render_openapi_types(_load_openapi())
    if args.check:
        if not OUTPUT_PATH.exists() or OUTPUT_PATH.read_text() != generated:
            print(
                "OpenAPI TypeScript contract is stale; run "
                "uv run python scripts/generate_openapi_types.py",
                file=sys.stderr,
            )
            return 1
        return 0
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
