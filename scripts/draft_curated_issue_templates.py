from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED = ROOT / "seeds" / "checklist-library.json"
DEFAULT_OUTPUT_DIR = ROOT / "seeds" / "issue-templates"
EXCLUDED_CHECKLISTS = {"product.software-development-plan"}
GENERIC_TITLES = {
    "必要内容覆盖不完整",
    "审核依据与结论不能相互支撑",
    "相关内容存在一致性缺口",
    "双向追踪关系不完整",
    "判定准则缺乏可验证性",
    "变更内容未完成同步闭环",
    "评审与批准记录不完整",
    "实际执行与既定过程存在偏差",
    "职责与接口边界不清晰",
    "风险与异常处理安排不足",
}

ACTION_RE = re.compile(
    r"(?:描述|定义|说明|记录|列出|标识|明确|建立|制定|识别|确定|指定|形成|"
    r"实施|执行|进行|开展|跟踪|统计|汇总|准备|获取|通知|通报|"
    r"验证|分析|评审|审批|发布|参加|沟通|保存|留存|"
    r"给出|设置|填写|比较|分解|落实)(?:了|的)?(?P<object>.+)"
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _without_examples(text: str) -> str:
    text = re.sub(r"（(?:包括|如|即)[^）]*）", "", text)
    text = re.sub(r"\((?:包括|如|即)[^)]*\)", "", text)
    return _normalize(text)


def _clip(text: str, limit: int = 80) -> str:
    text = _normalize(text).strip("，,；;。？?：:")
    if len(text) <= limit:
        return text
    for marker in ("，", "；", "且", "并", "以及", "或"):
        prefix = text.split(marker, 1)[0].strip()
        if 12 <= len(prefix) <= limit:
            return prefix
    return text[:limit].rstrip("，,；;、")


def _requirement_text(content: str) -> str:
    return _without_examples(content).strip("。？? ")


def _negative_requirement(content: str) -> str:
    text = _requirement_text(content)
    first = re.split(r"[。；;？?]", text, maxsplit=1)[0]
    first = _normalize(first)
    if first.startswith("是否"):
        body = first[2:]
        replacements = (
            ("符合", "不符合"),
            ("具有", "不具备"),
            ("具备", "不具备"),
            ("有", "缺少"),
            ("得到", "未得到"),
            ("覆盖", "未覆盖"),
            ("可以", "不能"),
            ("完整、准确", "未完整准确"),
            ("完整准确", "未完整准确"),
            ("完整、正确", "未完整正确"),
            ("完整正确", "未完整正确"),
            ("准确清晰", "未准确清晰"),
            ("清楚", "未清楚"),
            ("明确", "未明确"),
            ("正确", "未正确"),
            ("准确", "未准确"),
            ("完整", "不完整"),
            ("合理", "不合理"),
            ("按", "未按"),
        )
        for source, target in replacements:
            if body.startswith(source):
                first = target + body[len(source) :]
                break
        else:
            first = "未" + body
    else:
        first = re.sub(r"是否都", "未全部", first, count=1)
        first = re.sub(r"是否符合", "不符合", first, count=1)
        first = re.sub(r"是否保持一致", "未保持一致", first, count=1)
        first = re.sub(r"是否一致", "不一致", first, count=1)
        first = re.sub(r"是否清晰准确", "不清晰或不准确", first, count=1)
        first = re.sub(r"是否清晰、准确", "不清晰或不准确", first, count=1)
        first = re.sub(r"是否明确", "不明确", first, count=1)
        first = re.sub(r"是否正确", "不正确", first, count=1)
        first = re.sub(r"是否完整", "不完整", first, count=1)
        first = re.sub(r"是否合理", "不合理", first, count=1)
        first = re.sub(r"是否按", "未按", first, count=1)
        first = first.replace("有无得到", "未得到", 1)
        first = first.replace("有无进行", "未进行", 1)
        first = first.replace("有无制定", "未制定", 1)
        first = first.replace("有无建立", "未建立", 1)
        first = first.replace("有无形成", "未形成", 1)
        first = first.replace("有无记录", "未记录", 1)
        first = first.replace("有无", "未", 1)
        first = first.replace("是否", "未", 1)
    first = re.sub(r"(描述|定义|说明|记录|列出|标识|明确|建立|制定|识别|确定|"
                   r"指定|形成|实施|执行|进行|开展|跟踪|统计|汇总|准备|获取|"
                   r"分配|通知|通报|控制|管理|维护|验证|分析|评审|审批|发布|"
                   r"参加|参与|沟通|保存|留存|提供|给出|设置|填写|修订|更新)了", r"\1", first)
    first = first.replace("未完整、准确地", "未完整准确").replace("未完整、正确地", "未完整正确")
    first = first.replace("未准确地", "未准确").replace("未正确地", "未正确")
    first = first.replace("是否", "未").replace("有无", "未")
    first = first.replace("未一致", "不一致")
    consistency = re.fullmatch(r"(.+)未与(.+)一致", first)
    if consistency:
        first = f"{consistency.group(1)}与{consistency.group(2)}不一致"
    return _clip(first)


def _focus(content: str) -> tuple[str, str]:
    text = _requirement_text(content)
    text = re.split(r"[。；;？?]", text, maxsplit=1)[0]
    text = re.sub(r"^(?:当|若|对于|对)", "", text)
    text = text.replace("是否", "").replace("有无", "")
    if "一致" in text:
        before_consistent = text.rsplit("一致", 1)[0]
        if "与" in before_consistent:
            subject = before_consistent.rsplit("与", 1)[0].strip("，,；;。 ")
            if len(subject) >= 4:
                return _clip(subject, 30), "明确"
    match = ACTION_RE.search(text)
    action = "落实"
    value = text
    if match:
        action = match.group(0)[: match.group(0).find(match.group("object"))]
        action = action.rstrip("了的")
        value = match.group("object")
    value = re.split(r"[，；;？?]|且与|并与|并对|并进行|并制定|并明确", value, maxsplit=1)[0]
    value = re.sub(r"^(?:本项目的?|相关的?|所有|各项|每个)", "", value).strip()
    value = value.lstrip("的 ")
    if len(value) < 4:
        value = text
    return _clip(value, 30), action


def _quality_title(content: str) -> str:
    compliance = re.search(r"^(.{2,34}?)(?:是否)?符合", _without_examples(content))
    if compliance and not compliance.group(1).startswith("是否"):
        subject = compliance.group(1).replace("有无", "").strip("，,。；;？? ")
        return _clip(f"{subject}执行不规范或不完整")
    missing = _negative_requirement(content)
    if missing.startswith("缺少"):
        target = missing.removeprefix("缺少").strip()
        return _clip(f"{target}内容不完整或记录不规范")
    if "可重用性" in missing:
        return _clip(f"{missing.split('未', 1)[0]}的可重用性不足")
    for marker in ("不完整", "不准确", "不清晰", "不正确", "不明确"):
        if marker in missing:
            subject = missing.split(marker, 1)[0].strip("，,；;。 ")
            return _clip(f"{subject}存在遗漏、错误或表述不清")
    if "不一致" in missing:
        subject = missing.split("不一致", 1)[0].strip("，,；;。 ")
        return _clip(f"{subject}的一致性核对不充分")

    if "未" not in missing:
        return _clip(f"{missing}的落实内容不完整或不规范")
    subject, tail = missing.split("未", 1)
    subject = subject.strip("，,；;。 ")
    tail = tail.strip()
    if not subject:
        tail = re.sub(r"^为项目", "", tail)
    tail = re.sub(
        r"^(?:完整准确|完整正确|准确清晰|完整|准确|正确|清晰|合理|及时)(?:地)?",
        "",
        tail,
    )
    action_patterns = (
        (r"^(?:完整准确|完整正确|准确清晰|清楚|准确|正确)?(?:地)?(描述|定义|说明|记录|列出|标识|填写|给出)(.+)", "content"),
        (r"^(建立|分解)(.+)", "structure"),
        (r"^(制定|准备|设置)(.+)", "plan"),
        (r"^(明确|确定|指定|识别)(.+)", "clarity"),
        (r"^(跟踪|维护|管理|控制)(.+)", "closure"),
        (r"^(验证|分析|统计|比较)(.+)", "analysis"),
        (r"^(评审|审批|批准)(.+)", "review"),
        (r"^(实施|执行|进行|开展|参加|参与|沟通|通知|通报|发布|落实)(.+)", "execution"),
    )
    for pattern, kind in action_patterns:
        match = re.match(pattern, tail)
        if not match:
            continue
        action, obj = match.groups()
        obj = obj.lstrip("了的到 ")
        include_action = kind in {"execution", "review", "analysis", "closure"}
        target = _clip(f"{subject}{action if include_action else ''}{obj}", 56)
        suffixes = {
            "content": f"的{action}存在遗漏或错误",
            "structure": "覆盖不完整或关系不准确",
            "plan": "缺少必要内容或执行条件",
            "clarity": "识别不完整或表述不清",
            "closure": "执行不及时或未形成闭环",
            "analysis": "方法或结果不充分",
            "review": f"的{action}过程不符合规定",
            "execution": "的执行范围或过程不符合要求",
        }
        return _clip(target + suffixes[kind])
    positive = (subject + tail).strip()
    return _clip(_clip(positive, 60) + "落实不完整或不规范")


def _control_title(content: str) -> str | None:
    focus, _ = _focus(content)
    if any(word in content for word in ("追踪", "追溯", "映射")):
        if "追踪关系" in focus or "映射关系" in focus:
            return _clip(f"{focus}不完整或不可核验")
        return _clip(f"{focus}的追踪关系不完整或不可核验")
    if "一致" in content:
        reference = "规定依据"
        consistency_text = _without_examples(content)
        before_consistent = consistency_text.rsplit("一致", 1)[0]
        if "与" in before_consistent:
            reference = before_consistent.rsplit("与", 1)[-1]
            reference = reference.replace("是否", "").replace("保持", "")
            reference = reference.strip("，,。；;？?的要求 ") or reference
        else:
            tail = re.split(r"[，,；;。]", before_consistent)[-1]
            tail = tail.replace("是否", "").replace("保持", "").strip()
            if 2 <= len(tail) <= 20:
                return _clip(f"{tail}不一致")
        return _clip(f"{focus}与{reference}不一致")
    if any(word in content for word in ("变更", "修订", "更新")):
        return _clip(f"{focus}的变更控制或同步更新不到位")
    if "风险" in content:
        return _clip(f"{focus}的风险处置和跟踪安排不完整")
    if any(word in content for word in ("评审", "审核", "审批", "批准")):
        if any(word in focus for word in ("评审", "审核", "审批", "批准")):
            return _clip(f"{focus}记录不完整")
        return _clip(f"{focus}的评审审批记录不完整")
    if any(word in content for word in ("测试", "验证", "确认")):
        return _clip(f"{focus}缺少明确的范围、方法或判定准则")
    if any(word in content for word in ("记录", "报告", "清单", "纪要", "表")):
        if focus.endswith(("记录", "报告", "清单", "纪要", "表")):
            return _clip(f"{focus}缺失或无法追溯")
        return _clip(f"{focus}的记录缺失或无法追溯")
    if any(word in content for word in ("计划", "进度", "时机", "节点", "周期")):
        return _clip(f"{focus}的责任、时机或完成条件不明确")
    if any(word in content for word in ("人员", "职责", "组织", "责任人", "利益相关方")):
        return _clip(f"{focus}的职责分工或参与范围不清")
    return None


def _issue(
    key: str,
    title: str,
    description: str,
    suggestion: str,
) -> dict[str, str]:
    return {
        "stable_key": key,
        "title": title,
        "description": description,
        "corrective_suggestion": suggestion,
    }


def draft_item(content: str, starred: bool) -> dict[str, Any]:
    requirement = _requirement_text(content).replace("是否", "").replace("有无", "")
    if "模板" in content and any(
        word in content for word in ("格式", "排版", "规范性", "一致", "符合")
    ):
        severity = "严重" if starred else "一般"
        return {
            "default_severity": severity,
            "issues": [
                _issue(
                    "template-structure",
                    "未按适用模板编排文档结构",
                    f"文档的章节结构、栏目设置或编排方式未满足“{requirement}”的要求。",
                    "确认当前适用的受控模板，按照模板结构重新编排文档，并逐项核对章节和栏目。",
                ),
                _issue(
                    "format-elements",
                    "模板规定的格式要素填写不规范",
                    "标题层级、编号、表格、签署栏或其他模板格式要素存在缺失、错位或填写不规范。",
                    "逐项对照受控模板补齐格式要素，统一标题、编号和表格格式，并完成版式复核。",
                ),
            ],
        }
    if "适用" in content and "框架" in content:
        severity = "严重" if starred else "一般"
        return {
            "default_severity": severity,
            "issues": [
                _issue(
                    "business-fit",
                    "代码框架与业务需求不匹配",
                    "所选代码框架不能有效支持当前业务功能、运行约束或预期扩展方式。",
                    "结合业务需求、运行环境和扩展目标重新评估代码框架，必要时调整框架或补充适配设计。",
                ),
                _issue(
                    "architecture-constraints",
                    "代码框架的适用条件和技术约束未说明",
                    "虽选定了代码框架，但未说明适用范围、依赖条件、技术限制及不适用场景。",
                    "补充框架的适用范围、关键依赖和限制条件，并验证这些条件与项目实际环境一致。",
                ),
            ],
        }
    missing_title = _negative_requirement(content)
    quality_title = _quality_title(content)
    titles: list[str] = []
    issues: list[dict[str, str]] = []

    def add(key: str, title: str, description: str, suggestion: str) -> None:
        title = _clip(title)
        if title in titles:
            return
        titles.append(title)
        issues.append(_issue(key, title, description, suggestion))

    add(
        "requirement-missing",
        missing_title,
        f"检查发现，检查项要求的“{requirement}”未得到落实，相关文档、活动或结果中缺少对应内容。",
        f"依据适用要求补充并落实“{requirement}”，完成后对落实结果进行复核。",
    )
    add(
        "requirement-incomplete",
        quality_title,
        f"虽然已有相关内容或活动，但“{requirement}”仅得到部分落实，存在遗漏、不准确或不可执行的情况。",
        f"逐项对照“{requirement}”核对现有内容，补齐遗漏、修正错误，并确认修改后的结果能够执行和复查。",
    )
    control_title = _control_title(content)
    if control_title:
        add(
            "control-gap",
            control_title,
            f"与“{requirement}”相关的依据、过程控制或结果记录不完整，不能证明该项要求已按规定形成闭环。",
            f"围绕“{requirement}”补充必要的依据、责任、过程记录和结果确认，并核对相关内容保持一致。",
        )
    if len(issues) < 2:
        raise ValueError(f"Could not draft two distinct issues for: {content}")
    if any(issue["title"] in GENERIC_TITLES for issue in issues):
        raise ValueError(f"Generic title generated for: {content}")
    if any(re.search(r"是否|有无|[？?]", issue["title"]) for issue in issues):
        raise ValueError(f"Question form leaked into issue title for: {content}")
    return {
        "default_severity": "严重" if starred else "一般",
        "issues": issues,
    }


def draft_checklist(checklist: dict[str, Any]) -> dict[str, Any]:
    items: dict[str, Any] = {}
    for group in checklist["groups"]:
        for item in group["items"]:
            items[item["stable_key"]] = draft_item(item["content"], bool(item["starred"]))
    return {
        "schema_version": 1,
        "checklist_stable_key": checklist["stable_key"],
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Draft checklist-specific issue templates for checklists without curated content."
    )
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.seed.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    for checklist in payload["checklists"]:
        key = checklist["stable_key"]
        output = args.output_dir / f"{key}.json"
        if key in EXCLUDED_CHECKLISTS or (output.exists() and not args.force):
            skipped += 1
            continue
        output.write_text(
            json.dumps(draft_checklist(checklist), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written += 1
    print(f"written={written} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
