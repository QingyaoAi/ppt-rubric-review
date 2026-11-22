#!/usr/bin/env python3
"""Zero-dependency CLI for Handmade PPT Rubric v2.2.

Subcommands:
  guide   Print rubric criteria, anchors, vetoes, signals, and tier thresholds
  scan    Forensic XML/ZIP pre-scan of a .pptx for S1-S11 signals (including
          non-human URLs/hyperlinks, internal-draft citations, defensive notes),
          placeholders, verbatim repetition, leaked AI meta-text, and sampling plan
  init    Create a blank evidence-backed v2.2 review JSON (auto-activates H1-H9)
  score   Validate and score a review JSON (Total, Anti-AI, A-H dims, S-D tier, fix_queue)
  diff    Compare two review rounds (deltas in Total, Anti-AI, vetoes, red flags, items)
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
import posixpath
import re
import sys
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

RUBRIC_JSON = Path(__file__).resolve().parents[1] / "references" / "rubric-v2.2.json"
NA = "不适用"

NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}

GENRE_ALIASES = {
    "01": "01",
    "01 人才与项目汇报": "01",
    "人才": "01",
    "项目": "01",
    "汇报": "01",
    "02": "02",
    "02 科技奖答辩": "02",
    "科技奖": "02",
    "答辩": "02",
    "03": "03",
    "03 学术报告": "03",
    "学术": "03",
    "04": "04",
    "04 教学讲授": "04",
    "教学": "04",
    "课程": "04",
    "other": "other",
    "其他": "other",
}

META_LEAK_PATTERNS = [
    r"被裁掉的要点",
    r"口径都记在备注",
    r"以下由\s*AI\s*生成",
    r"待\s*AI\s*补充",
    r"此处插入",
    r"\[待补充\]",
    r"【待填[^】]*】",
    r"[（(]待填[^）)]*[）)]",
    r"[（(]待修改[）)]",
    r"[（(]请填写[^）)]*[）)]",
    r"遗留问题",
    r"\bTODO\b",
    r"\bTBD\b",
    r"\bFIXME\b",
    r"示例编号",
]

BUZZWORD_PATTERNS = [
    "赋能千行百业",
    "数智新生态",
    "全链路闭环",
    "高质量跃升",
    "多维赋能",
    "生态闭环",
    "深度赋能",
    "智慧抓手",
    "提质增效赋能",
    "全方位赋能",
]

# S11: Non-human citation links, raw search syntax, internal-file citations, defensive notes
UNHUMAN_URL_RX = re.compile(
    r"(?:https?://\S*(?:utm_source=|utm_medium=|utm_campaign=|utm_content=|grounding-api-redirect|"
    r"vertexaisearch\.cloud\.google\.com|__biz=|fbclid=|gclid=|spm=|share_token=)\S*"
    r"|https?://(?:www\.)?example\.(?:com|org|net)\S*"
    r"|https?://doi\.org/10\.xxxx/\S*"
    r"|https?://\S{95,}\?\S*&\S*"
    r"|\[[^\]]{1,60}\]\(https?://[^\)]+\)"
    r"|【\d+†[^】]*】"
    r"|\[(?:web|cite|ref):\d+\]"
    r"|\bturn\d+search\d+\b"
    r"|\bcite_(?:start|end)\b)",
    re.IGNORECASE,
)

INTERNAL_CITE_RX = re.compile(
    r"(?:(?:来源|出处|参考|数据来源|图源)[:：\s]{0,4}[^\n]{0,36}"
    r"(?:底表|原表|建议稿|讨论稿|合并版|整理稿|修订稿|初稿|草稿|内部材料|课题组整理|内部统计|"
    r"公开资料整理|调研整理|复算脚本|交付说明|任务书|补充明细|合并对照|汇总表|"
    r"\.md\b|\.docx?\b|\.xlsx?\b|\.csv\b|\.txt\b|workspace/)"
    r"|(?:source|ref|citation)\s*[:：]\s*[^\n]{0,36}"
    r"(?:internal\s+(?:material|doc|note|data)|public\s+sources|draft[_\s-]?v\d|"
    r"\.md\b|\.docx?\b|\.xlsx?\b|\.csv\b|workspace/))",
    re.IGNORECASE,
)

DEFENSIVE_NOTE_RX = re.compile(
    r"(?:留档可查|避免口径争议|含时间戳[^\n]{0,12}可复核|结题前复核|经交叉复算|与验收材料一致|"
    r"本图(?:根据|基于)[^\n]{0,18}(?:整理|绘制)[^\n]{0,10}(?:仅作示意|仅供参考)|仅作示意[^\n]{0,10}不代表|"
    r"数据为(?:模拟|估算|示意)|证据核验截至|口径见项目任务书|"
    r"for\s+illustrative\s+purposes\s+only|illustrative\s+data\s+only|simulated\s+data\s+only|"
    r"verified\s+as\s+of\s+\d{4}-\d{2}|methodology\s+consistent\s+with\s+(?:the\s+)?project\s+charter)",
    re.IGNORECASE,
)

VAGUE_SOURCE_STAMP_RX = re.compile(
    r"^\s*(?:出处|来源|source)\s*[:：]\s*[^\n]{0,40}"
    r"(?:课题组|内部|调研|整理|公开资料|项目计划|任务书|评测|底表|合并版|建议稿|internal|public\s+sources)",
    re.IGNORECASE,
)


def load_rubric(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != "v2.2":
        raise ValueError("仅支持 v2.2 JSON 量表（references/rubric-v2.2.json）。")
    return data


def normalize_genre(raw: str) -> str:
    key = raw.strip()
    if key in GENRE_ALIASES:
        return GENRE_ALIASES[key]
    raise ValueError(f"未知体裁 {raw!r}，可选：01 / 02 / 03 / 04 / other")


def rounded(value: Fraction, places: int = 2) -> Decimal:
    scale = 10 ** places
    units = int(value * scale + Fraction(1, 2))
    return Decimal(units) / scale


def active_criteria_ids(rubric: dict, genre_key: str) -> tuple[list[str], list[str]]:
    active_h = set(rubric["genres"][genre_key]["active_h"])
    page_ids = []
    overall_ids = []
    for code, item in rubric["criteria"].items():
        if item["dim"] == "H" and code not in active_h:
            continue
        if item["scope"] == "逐页":
            page_ids.append(code)
        else:
            overall_ids.append(code)
    return page_ids, overall_ids


# =============================================================================
# SUBCOMMAND: guide
# =============================================================================
def cmd_guide(rubric: dict, genre: str | None, fmt: str) -> str:
    if fmt == "json":
        if genre:
            gk = normalize_genre(genre)
            p_ids, o_ids = active_criteria_ids(rubric, gk)
            keep = set(p_ids) | set(o_ids)
            filtered = dict(rubric)
            filtered["selected_genre"] = rubric["genres"][gk]
            filtered["criteria"] = {k: v for k, v in rubric["criteria"].items() if k in keep}
            return json.dumps(filtered, ensure_ascii=False, indent=2)
        return json.dumps(rubric, ensure_ascii=False, indent=2)

    guides = rubric.get("core_guidelines") or rubric.get("personal_guidelines") or []
    lines = [
        f"# {rubric['title']}",
        "",
        f"- 默认修订目标分：**{rubric['default_target']}**（S 级门槛：整体 ≥88 且去 AI 味 ≥85，0 否决，0 红灯，0 整稿零分，含 0 分页 ≤10%）",
        "- 核心制作底线（P1–P6）：" + "；".join(f"{p['id']} {p['rule']}" for p in guides),
        "",
        "## 一、交付否决项（V1–V4，命中任一即 D 级阻断）",
    ]
    for vid, v in rubric["vetoes"].items():
        lines.append(f"- **{vid} {v['title']}**：{v['condition']}")

    lines.append("")
    lines.append("## 二、AI 味与工序风险指纹（S1–S11，0 无 / 1 轻度 / 2 显著红灯）")
    for sid, s in rubric["signals"].items():
        tag = "工序" if s["category"] == "process" else "AI味"
        lines.append(f"- **{sid} [{tag}] {s['title']}**：{s['criteria']}")

    lines.append("")
    if genre:
        gk = normalize_genre(genre)
        p_ids, o_ids = active_criteria_ids(rubric, gk)
        keep = set(p_ids) | set(o_ids)
        lines.append(f"## 三、当前体裁启用条目（{rubric['genres'][gk]['label']}，共 {len(keep)} 项 / 满权 {sum(rubric['criteria'][k]['weight'] for k in keep)}）")
    else:
        keep = set(rubric["criteria"].keys())
        lines.append("## 三、全部评分条目（23 通用 88 权 + 9 预置体裁项启用 3 项 12 权 = 26 实评项 / 满权 100）")

    for code, item in rubric["criteria"].items():
        if code not in keep:
            continue
        star = "★" if item["anti_ai"] else ""
        na_tag = "，可不适用" if item["allows_na"] else ""
        lines.append(f"### {code}{star} {item['title']}（{item['scope']}，{item['weight']} 权{na_tag}）")
        lines.append(f"- 核查：{item['how']}")
        lines.append(f"- 0 分：{item['anchors']['0']}")
        lines.append(f"- 1 分：{item['anchors']['1']}")
        lines.append(f"- 2 分：{item['anchors']['2']}")
    return "\n".join(lines)


# =============================================================================
# SUBCOMMAND: scan (Pure-stdlib PPTX forensic scanner)
# =============================================================================
def _norm_target(base_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    base_dir = posixpath.dirname(base_part)
    return posixpath.normpath(posixpath.join(base_dir, target))


def _read_rels(zf: ZipFile, part_path: str) -> dict[str, tuple[str, str]]:
    rels_path = posixpath.join(posixpath.dirname(part_path), "_rels", posixpath.basename(part_path) + ".rels")
    if rels_path not in zf.namelist():
        return {}
    root = ET.fromstring(zf.read(rels_path))
    out = {}
    for rel in root.findall("rel:Relationship", NS):
        rid = rel.attrib.get("Id", "")
        rtype = rel.attrib.get("Type", "")
        tgt = rel.attrib.get("Target", "")
        if rel.attrib.get("TargetMode") == "External":
            out[rid] = (rtype, tgt)
        else:
            out[rid] = (rtype, _norm_target(part_path, tgt))
    return out


def _slide_paths_in_order(zf: ZipFile) -> tuple[list[str], int, int]:
    pres = ET.fromstring(zf.read("ppt/presentation.xml"))
    rels = _read_rels(zf, "ppt/presentation.xml")
    sld_sz = pres.find("p:sldSz", NS)
    sw = int(sld_sz.attrib.get("cx", "12192000")) if sld_sz is not None else 12192000
    sh = int(sld_sz.attrib.get("cy", "6858000")) if sld_sz is not None else 6858000

    paths = []
    for sld_id in pres.findall("p:sldIdLst/p:sldId", NS):
        rid = sld_id.attrib.get(f"{{{NS['r']}}}id", "")
        if rid in rels:
            paths.append(rels[rid][1])
    return paths, sw, sh


def _extract_text_runs(root: ET.Element) -> tuple[list[str], list[int]]:
    paras = []
    sizes = []
    for p in root.findall(".//a:p", NS):
        buf = []
        for r in p.findall(".//a:r", NS):
            rpr = r.find("a:rPr", NS)
            if rpr is not None and "sz" in rpr.attrib:
                try:
                    sizes.append(int(rpr.attrib["sz"]))
                except ValueError:
                    pass
            t = r.find("a:t", NS)
            if t is not None and t.text:
                buf.append(t.text)
        for fld in p.findall(".//a:fld", NS):
            t = fld.find("a:t", NS)
            if t is not None and t.text:
                buf.append(t.text)
        line = "".join(buf).strip()
        if line:
            paras.append(line)
    return paras, sizes


def scan_pptx(pptx_path: Path) -> dict:
    if not pptx_path.exists():
        raise FileNotFoundError(f"找不到 PPT 文件：{pptx_path}")

    with ZipFile(pptx_path) as zf:
        names = set(zf.namelist())
        slide_paths, sw, sh = _slide_paths_in_order(zf)
        total = len(slide_paths)
        if total == 0:
            raise ValueError("PPT 中没有检测到幻灯片。")

        slide_area = max(1, sw * sh)
        media_files = [n for n in names if n.startswith("ppt/media/") and not n.endswith("/")]
        image_exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".svg", ".emf", ".wmf"}
        image_media = [n for n in media_files if Path(n).suffix.lower() in image_exts]

        # Pre-cache layout names
        layout_names: dict[str, str] = {}
        for n in names:
            if n.startswith("ppt/slideLayouts/slideLayout") and n.endswith(".xml"):
                try:
                    lr = ET.fromstring(zf.read(n))
                    cs = lr.find("p:cSld", NS)
                    layout_names[n] = (cs.attrib.get("name", "") if cs is not None else "")
                except ET.ParseError:
                    layout_names[n] = ""

        hidden_slides = []
        blank_count = 0
        title_ph_count = 0
        all_sizes: list[int] = []
        fullbleed_img_counter: Counter[str] = Counter()
        fullbleed_slides: dict[str, list[int]] = {}
        total_body_chars = 0
        total_cjk_chars = 0
        total_cn_periods = 0
        total_notes_chars = 0
        notes_cite_tags = 0
        slide_cite_tags = 0
        hand_pg_slides = 0
        hand_pg_mismatches = []
        bottom_bar_slides = []
        large_table_slides = []
        chart_slides = []
        dense_slides = []
        meta_leaks = []
        buzzword_hits = []
        unhuman_url_hits = []
        internal_cite_hits = []
        defensive_note_hits = []
        vague_source_stamp_slides = []
        sentence_slides: dict[str, list[int]] = {}
        same_slide_dups = []
        slide_summaries = []

        for idx, spath in enumerate(slide_paths, start=1):
            root = ET.fromstring(zf.read(spath))
            if root.attrib.get("show") == "0":
                hidden_slides.append(idx)

            srels = _read_rels(zf, spath)
            layout_name = ""
            notes_path = None
            has_chart = False
            slide_img_targets = set()

            for rid, (rtype, tgt) in srels.items():
                if rtype.endswith("/slideLayout"):
                    layout_name = layout_names.get(tgt, "")
                elif rtype.endswith("/notesSlide"):
                    notes_path = tgt
                elif rtype.endswith("/chart"):
                    has_chart = True
                elif rtype.endswith("/image"):
                    slide_img_targets.add(tgt)
                elif rtype.endswith("/hyperlink") and UNHUMAN_URL_RX.search(tgt):
                    unhuman_url_hits.append({"slide": idx, "where": "hyperlink_rel", "match": tgt[:80]})

            if re.search(r"(blank|空白)", layout_name, re.IGNORECASE):
                blank_count += 1
            if has_chart:
                chart_slides.append(idx)

            # Check title placeholder
            has_title_ph = False
            for ph in root.findall(".//p:ph", NS):
                ptype = ph.attrib.get("type", "")
                if ptype in ("title", "ctrTitle"):
                    has_title_ph = True
                    break
            if has_title_ph:
                title_ph_count += 1

            # Check full-bleed images (S1)
            for pic in root.findall(".//p:pic", NS):
                blip = pic.find(".//a:blip", NS)
                xfrm = pic.find(".//p:spPr/a:xfrm", NS)
                if blip is not None and xfrm is not None:
                    emb = blip.attrib.get(f"{{{NS['r']}}}embed", "")
                    ext = xfrm.find("a:ext", NS)
                    if emb in srels and ext is not None:
                        cx = int(ext.attrib.get("cx", "0"))
                        cy = int(ext.attrib.get("cy", "0"))
                        if (cx * cy) / slide_area >= 0.85:
                            img_t = srels[emb][1]
                            fullbleed_img_counter[img_t] += 1
                            fullbleed_slides.setdefault(img_t, []).append(idx)

            # Check shapes: bottom bars (S7), hand-typed page numbers (S5)
            has_bottom_bar = False
            hand_num_found = None
            for sp in root.findall(".//p:sp", NS):
                xfrm = sp.find("p:spPr/a:xfrm", NS)
                sp_paras, _ = _extract_text_runs(sp)
                sp_txt = " ".join(sp_paras).strip()
                has_sldnum_fld = any(
                    fld.attrib.get("type") == "slidenum" for fld in sp.findall(".//a:fld", NS)
                )
                if xfrm is not None:
                    off = xfrm.find("a:off", NS)
                    ext = xfrm.find("a:ext", NS)
                    if off is not None and ext is not None:
                        x = int(off.attrib.get("x", "0"))
                        y = int(off.attrib.get("y", "0"))
                        cx = int(ext.attrib.get("cx", "0"))
                        cy = int(ext.attrib.get("cy", "0"))
                        # Bottom strip / callout bar: low on slide, wide strip
                        if y / sh >= 0.78 and cx / sw >= 0.55 and cy / sh <= 0.18 and sp_txt:
                            has_bottom_bar = True
                        # Corner small integer without slidenum field
                        if (
                            not has_sldnum_fld
                            and re.fullmatch(r"\d{1,3}", sp_txt)
                            and (y / sh >= 0.82 or y / sh <= 0.12)
                            and cx / sw <= 0.15
                        ):
                            val = int(sp_txt)
                            if 1 <= val <= max(total + 50, 200):
                                hand_num_found = val

            if has_bottom_bar:
                bottom_bar_slides.append(idx)
            if hand_num_found is not None:
                hand_pg_slides += 1
                if hand_num_found != idx:
                    hand_pg_mismatches.append({"slide": idx, "printed": hand_num_found})

            # Check large tables (S8)
            for gf in root.findall(".//p:graphicFrame", NS):
                tbl = gf.find(".//a:tbl", NS)
                if tbl is None:
                    continue
                rows = tbl.findall("a:tr", NS)
                xfrm = gf.find("p:xfrm", NS)
                area_ratio = 0.0
                if xfrm is not None:
                    ext = xfrm.find("a:ext", NS)
                    if ext is not None:
                        area_ratio = (int(ext.attrib.get("cx", "0")) * int(ext.attrib.get("cy", "0"))) / slide_area
                if len(rows) >= 8 or area_ratio >= 0.25:
                    large_table_slides.append({"slide": idx, "rows": len(rows), "area_pct": round(area_ratio * 100, 1)})
                    break

            # Slide text & font sizes
            paras, sizes = _extract_text_runs(root)
            all_sizes.extend(sizes)
            body_text = "\n".join(paras)
            body_len = len(re.sub(r"\s+", "", body_text))
            cjk_cnt = len(re.findall(r"[\u4e00-\u9fff]", body_text))
            cn_per = body_text.count("。")
            total_body_chars += body_len
            total_cjk_chars += cjk_cnt
            total_cn_periods += cn_per
            slide_cite_tags += len(re.findall(r"\[S\d+\]", body_text))

            if body_len >= 600:
                dense_slides.append({"slide": idx, "chars": body_len})

            # Meta-text / placeholder leaks
            for pat in META_LEAK_PATTERNS:
                m = re.search(pat, body_text)
                if m:
                    meta_leaks.append({"slide": idx, "match": m.group(0)})
            if any(p.strip() in ("……", "...", "——") for p in paras):
                meta_leaks.append({"slide": idx, "match": "standalone_ellipsis_placeholder"})

            # Buzzwords
            for bw in BUZZWORD_PATTERNS:
                if bw in body_text:
                    buzzword_hits.append({"slide": idx, "term": bw})

            # S11: Non-human URLs/syntax, internal-file citations, defensive notes, vague source stamps
            for m in UNHUMAN_URL_RX.finditer(body_text):
                unhuman_url_hits.append({"slide": idx, "where": "slide_text", "match": m.group(0)[:80]})
            for m in INTERNAL_CITE_RX.finditer(body_text):
                internal_cite_hits.append({"slide": idx, "match": m.group(0)[:80]})
            for m in DEFENSIVE_NOTE_RX.finditer(body_text):
                defensive_note_hits.append({"slide": idx, "match": m.group(0)[:80]})
            if any(VAGUE_SOURCE_STAMP_RX.search(p) for p in paras):
                vague_source_stamp_slides.append(idx)

            # Verbatim sentence repetition across slides & duplicate lines within slide
            norm_lines = [re.sub(r"\s+", " ", p).strip() for p in paras if len(re.sub(r"\s+", "", p)) >= 20]
            seen_local = Counter(norm_lines)
            for ln, cnt in seen_local.items():
                if cnt >= 2:
                    same_slide_dups.append({"slide": idx, "count": cnt, "text": ln[:60]})
            for ln in set(norm_lines):
                sentence_slides.setdefault(ln, []).append(idx)

            # Speaker notes
            notes_len = 0
            if notes_path and notes_path in names:
                nroot = ET.fromstring(zf.read(notes_path))
                nparas, _ = _extract_text_runs(nroot)
                ntext = "\n".join(nparas)
                notes_len = len(re.sub(r"\s+", "", ntext))
                total_notes_chars += notes_len
                notes_cite_tags += len(re.findall(r"\[S\d+\]", ntext))

            title_guess = paras[0][:50] if paras else ""
            slide_summaries.append({
                "slide": idx,
                "hidden": idx in hidden_slides,
                "layout": layout_name,
                "body_chars": body_len,
                "notes_chars": notes_len,
                "images": len(slide_img_targets),
                "has_chart": has_chart,
                "title_preview": title_guess,
            })

    # Aggregate metrics
    blank_pct = round(100.0 * blank_count / total, 1)
    title_ph_pct = round(100.0 * title_ph_count / total, 1)
    qpt_cnt = sum(1 for s in all_sizes if s % 100 in (25, 75))
    quarter_pt_pct = round(100.0 * qpt_cnt / len(all_sizes), 1) if all_sizes else 0.0
    tiny_font_pct = round(100.0 * sum(1 for s in all_sizes if s < 1000) / len(all_sizes), 1) if all_sizes else 0.0
    notes_ratio = round(total_notes_chars / max(1, total_body_chars), 2)
    cn_period_per_1k = round(1000.0 * total_cn_periods / total_cjk_chars, 1) if total_cjk_chars >= 100 else None
    bottom_bar_pct = round(100.0 * len(bottom_bar_slides) / total, 1)
    large_table_pct = round(100.0 * len(large_table_slides) / total, 1)
    chart_pct = round(100.0 * len(chart_slides) / total, 1)
    hand_pg_pct = round(100.0 * hand_pg_slides / total, 1)
    hand_pg_mismatch_pct = round(100.0 * len(hand_pg_mismatches) / hand_pg_slides, 1) if hand_pg_slides else 0.0

    top_fb_img, top_fb_cnt = fullbleed_img_counter.most_common(1)[0] if fullbleed_img_counter else (None, 0)
    top_fb_pct = round(100.0 * top_fb_cnt / total, 1)
    non_bg_media = max(0, len(image_media) - (1 if top_fb_img and top_fb_pct >= 50.0 else 0))

    repeated_boilerplate = [
        {"slides": sls, "count": len(sls), "text": txt[:70]}
        for txt, sls in sentence_slides.items()
        if len(sls) >= 3
    ]
    repeated_boilerplate.sort(key=lambda x: -x["count"])

    # Suggested S1-S11 preliminary flags (human/visual review always confirms final levels!)
    vague_stamp_pct = round(100.0 * len(vague_source_stamp_slides) / total, 1)
    s11_total = len(unhuman_url_hits) + len(internal_cite_hits) + len(defensive_note_hits)
    s_hints = {}
    s_hints["S1"] = 2 if (top_fb_pct >= 50.0 and non_bg_media <= 5) else (1 if top_fb_pct >= 30.0 else 0)
    s_hints["S2"] = 2 if (blank_pct >= 80.0 and title_ph_pct <= 10.0) else (1 if blank_pct >= 50.0 else 0)
    s_hints["S3"] = 2 if quarter_pt_pct >= 30.0 else (1 if quarter_pt_pct >= 10.0 else 0)
    s_hints["S4"] = 2 if notes_ratio >= 5.0 else (1 if notes_ratio >= 2.0 else 0)
    s_hints["S5"] = 2 if (hand_pg_pct >= 30.0 and hand_pg_mismatch_pct > 50.0) else (1 if len(hand_pg_mismatches) >= 2 else 0)
    s_hints["S6"] = 2 if (notes_cite_tags >= 10 and slide_cite_tags == 0) else (1 if notes_cite_tags >= 5 and slide_cite_tags < notes_cite_tags / 3 else 0)
    s_hints["S7"] = 2 if bottom_bar_pct >= 60.0 else (1 if bottom_bar_pct >= 35.0 else 0)
    s_hints["S8"] = 2 if ((large_table_pct >= 25.0 and non_bg_media <= 5) or (chart_pct >= 20.0 and non_bg_media <= 3)) else (1 if (non_bg_media <= 5 and total >= 15) else 0)
    s_hints["S9"] = 2 if len(buzzword_hits) >= 4 else (1 if len(buzzword_hits) >= 1 else 0)
    s_hints["S10"] = 2 if (len(repeated_boilerplate) >= 2 or len(same_slide_dups) >= 2 or any("被裁掉" in m["match"] or "口径都记在备注" in m["match"] for m in meta_leaks)) else (1 if repeated_boilerplate or same_slide_dups else 0)
    s_hints["S11"] = (
        2
        if (
            len(unhuman_url_hits) >= 2
            or len(internal_cite_hits) >= 3
            or len(defensive_note_hits) >= 2
            or (vague_stamp_pct >= 40.0 and len(vague_source_stamp_slides) >= 5)
            or s11_total >= 3
        )
        else (1 if (s11_total >= 1 or (vague_stamp_pct >= 25.0 and len(vague_source_stamp_slides) >= 3)) else 0)
    )

    # Stratified sampling recommendation
    if total <= 35:
        rec_mode = "full"
        sample_slides = list(range(1, total + 1))
    else:
        rec_mode = "sample"
        picks = {1, 2, total - 1, total}
        for frac in (0.15, 0.28, 0.42, 0.56, 0.70, 0.84):
            picks.add(max(1, min(total, round(total * frac))))
        if dense_slides:
            picks.add(max(dense_slides, key=lambda x: x["chars"])["slide"])
        if large_table_slides:
            picks.add(large_table_slides[0]["slide"])
        if chart_slides:
            picks.add(chart_slides[0])
        sample_slides = sorted(picks)[:12]

    s1_6_red_hint = sum(1 for k in ("S1", "S2", "S3", "S4", "S5", "S6") if s_hints[k] == 2)
    s7_11_red_hint = sum(1 for k in ("S7", "S8", "S9", "S10", "S11") if s_hints[k] == 2)

    return {
        "file": str(pptx_path),
        "total_slides": total,
        "hidden_slides": hidden_slides,
        "metrics": {
            "unique_image_media": len(image_media),
            "non_bg_image_media": non_bg_media,
            "top_fullbleed_bg": top_fb_img,
            "top_fullbleed_bg_pct": top_fb_pct,
            "blank_layout_pct": blank_pct,
            "title_placeholder_pct": title_ph_pct,
            "quarter_pt_font_pct": quarter_pt_pct,
            "tiny_font_under_10pt_pct": tiny_font_pct,
            "total_body_chars": total_body_chars,
            "total_notes_chars": total_notes_chars,
            "notes_body_ratio": notes_ratio,
            "cn_period_per_1k_cjk": cn_period_per_1k,
            "hand_typed_pagenum_pct": hand_pg_pct,
            "hand_typed_pagenum_mismatch_pct": hand_pg_mismatch_pct,
            "notes_cite_tags": notes_cite_tags,
            "slide_cite_tags": slide_cite_tags,
            "bottom_bar_pct": bottom_bar_pct,
            "large_table_pct": large_table_pct,
            "native_chart_pct": chart_pct,
            "vague_source_stamp_pct": vague_stamp_pct,
        },
        "signal_hints": s_hints,
        "risk_summary": {
            "s1_6_process_red_hints": s1_6_red_hint,
            "s7_11_ai_flavor_red_hints": s7_11_red_hint,
            "v4_process_warning": s1_6_red_hint >= 2,
            "zero_period_warning": cn_period_per_1k == 0.0,
            "robotic_period_warning": (cn_period_per_1k is not None and cn_period_per_1k >= 25.0),
        },
        "findings": {
            "meta_leaks_or_placeholders": meta_leaks[:20],
            "buzzword_hits": buzzword_hits[:15],
            "unhuman_urls_or_syntax": unhuman_url_hits[:15],
            "internal_workflow_citations": internal_cite_hits[:15],
            "defensive_meta_notes": defensive_note_hits[:15],
            "vague_source_stamp_slides": vague_source_stamp_slides[:20],
            "repeated_boilerplate": repeated_boilerplate[:10],
            "same_slide_duplicates": same_slide_dups[:10],
            "dense_slides_over_600_chars": dense_slides[:10],
            "large_table_slides": large_table_slides[:10],
            "hand_pagenum_mismatches": hand_pg_mismatches[:15],
        },
        "recommended_review": {
            "mode": rec_mode,
            "sample_slides": sample_slides,
        },
        "slides": slide_summaries,
    }


def format_scan_summary(res: dict) -> str:
    m = res["metrics"]
    sh = res["signal_hints"]
    rs = res["risk_summary"]
    f = res["findings"]
    lines = [
        f"=== PPTX 客观预检报告（v2.2）：{Path(res['file']).name} ===",
        f"总页数：{res['total_slides']}（隐藏页：{len(res['hidden_slides'])}）| 独立图片媒体：{m['unique_image_media']}（非底图：{m['non_bg_image_media']}）",
        f"工序指标：满版单底图 {m['top_fullbleed_bg_pct']}% | Blank {m['blank_layout_pct']}% / 标题占位符 {m['title_placeholder_pct']}% | 四分点字号 {m['quarter_pt_font_pct']}% | 备注/正文 {m['notes_body_ratio']}x | 手打错页码 {len(f['hand_pagenum_mismatches'])}页 | 备注[Sxx] {m['notes_cite_tags']} vs 页面 {m['slide_cite_tags']}",
        f"AI味指标：底栏重复 {m['bottom_bar_pct']}% | 大表占比 {m['large_table_pct']}% | 原生柱图占比 {m['native_chart_pct']}% | 中文句号密度 {m['cn_period_per_1k_cjk']}‰ | <10pt碎字 {m['tiny_font_under_10pt_pct']}% | 笼统出处盖章 {m['vague_source_stamp_pct']}%",
        "预判红灯提示（须结合视觉渲染终审）："
        + " ".join(f"{k}={v}" for k, v in sh.items()),
    ]
    if rs["v4_process_warning"]:
        lines.append("⚠ [V4 高危预警] S1–S6 工序红灯预判 >=2 项，疑似重度非原生 AI 导出！")
    if rs["zero_period_warning"]:
        lines.append("⚠ [D2 预警] 中文正文 0 句号，疑似文档切页碎词堆砌！")
    if rs["robotic_period_warning"]:
        lines.append("⚠ [D2 预警] 中文句号密度 >=25‰，疑似机械句号迎合腔！")
    if f["unhuman_urls_or_syntax"]:
        sample_u = ", ".join(f"p{x['slide']}({x['where']}):{x['match'][:40]}" for x in f["unhuman_urls_or_syntax"][:4])
        lines.append(f"⚠ [S11/V3 非人链接或检索源码] ({len(f['unhuman_urls_or_syntax'])}处)：{sample_u}")
    if f["internal_workflow_citations"]:
        sample_i = ", ".join(f"p{x['slide']}:{x['match'][:36]}" for x in f["internal_workflow_citations"][:4])
        lines.append(f"⚠ [S11/F4 内部草稿/底表式出处] ({len(f['internal_workflow_citations'])}处)：{sample_i}")
    if f["defensive_meta_notes"]:
        sample_d = ", ".join(f"p{x['slide']}:{x['match'][:36]}" for x in f["defensive_meta_notes"][:4])
        lines.append(f"⚠ [S11/D2 防审计式反常口径/免责说明] ({len(f['defensive_meta_notes'])}处)：{sample_d}")
    if f["meta_leaks_or_placeholders"]:
        sample_m = ", ".join(f"p{x['slide']}:{x['match']}" for x in f["meta_leaks_or_placeholders"][:6])
        lines.append(f"⚠ [V3/G2 占位或元文本线索] ({len(f['meta_leaks_or_placeholders'])}处)：{sample_m}")
    if f["repeated_boilerplate"]:
        lines.append(f"⚠ [S10/D3 跨页照搬套话] ({len(f['repeated_boilerplate'])}条)：p{f['repeated_boilerplate'][0]['slides']}「{f['repeated_boilerplate'][0]['text']}」")
    if f["same_slide_duplicates"]:
        lines.append(f"⚠ [S10 同页重复条目] ({len(f['same_slide_duplicates'])}处)：p{f['same_slide_duplicates'][0]['slide']}×{f['same_slide_duplicates'][0]['count']}")
    if f["buzzword_hits"]:
        lines.append(f"⚠ [S9/D2 空词预警] ({len(f['buzzword_hits'])}处)：" + ", ".join(f"p{x['slide']}:{x['term']}" for x in f["buzzword_hits"][:6]))
    rec = res["recommended_review"]
    lines.append(f"推荐评审模式：{rec['mode']}（抽检/全评页序：{rec['sample_slides']}）")
    return "\n".join(lines)


# =============================================================================
# SUBCOMMAND: init
# =============================================================================
def new_record(
    rubric: dict,
    genre_key: str,
    page_count: int,
    deck: str,
    mode: str,
    total_slides: int,
    slide_numbers: list[int] | None,
    target: Decimal,
) -> dict:
    if page_count < 1 or page_count > 300:
        raise ValueError("本次评审页数须为 1–300 的正整数。")
    if total_slides < page_count:
        total_slides = page_count
    p_ids, o_ids = active_criteria_ids(rubric, genre_key)
    if slide_numbers is not None:
        if len(slide_numbers) != page_count:
            raise ValueError("--slide-numbers 数量须与 --pages 一致。")
        s_nums = slide_numbers
    else:
        s_nums = list(range(1, page_count + 1))

    return {
        "rubric_version": rubric["version"],
        "deck": deck,
        "genre": genre_key,
        "genre_label": rubric["genres"][genre_key]["label"],
        "mode": mode,
        "total_slides": total_slides,
        "page_count": page_count,
        "target": float(target),
        "vetoes": {vid: {"status": None, "evidence": ""} for vid in rubric["vetoes"]},
        "signals": {sid: {"level": None, "evidence": ""} for sid in rubric["signals"]},
        "overall": {code: {"score": None, "evidence": ""} for code in o_ids},
        "pages": [
            {
                "review_index": i + 1,
                "slide_no": s_nums[i],
                "role": "",
                "items": {code: {"score": None, "evidence": ""} for code in p_ids},
            }
            for i in range(page_count)
        ],
        "changes": [],
    }


# =============================================================================
# SUBCOMMAND: score
# =============================================================================
def _check_dict(val, loc: str, allowed_keys: set[str] | None = None) -> dict:
    if not isinstance(val, dict):
        raise ValueError(f"{loc} 应为 JSON 对象。")
    if allowed_keys is not None:
        unknown = set(val.keys()) - allowed_keys
        if unknown:
            raise ValueError(f"{loc} 含未知字段：{', '.join(sorted(unknown))}")
    return val


def _has_text(val, loc: str) -> bool:
    if not isinstance(val, str):
        raise ValueError(f"{loc} 应为字符串。")
    return bool(val.strip())


def assess(record: dict, rubric: dict) -> dict:
    record = _check_dict(record, "评审记录")
    if record.get("rubric_version") != rubric["version"]:
        raise ValueError(f"rubric_version 须为 {rubric['version']}。")
    genre_key = normalize_genre(str(record.get("genre", "")))
    n = record.get("page_count")
    if type(n) is not int or not 1 <= n <= 300:
        raise ValueError("page_count 须为 1–300 的整数。")
    total_slides = record.get("total_slides", n)
    if type(total_slides) is not int or total_slides < 1:
        raise ValueError("total_slides 须为正整数。")
    target = Decimal(str(record.get("target", rubric["default_target"])))
    if not 0 <= target <= 100:
        raise ValueError("target 须在 0–100 之间。")

    page_ids, overall_ids = active_criteria_ids(rubric, genre_key)
    criteria = rubric["criteria"]
    pending: list[str] = []

    # 1. Vetoes (V1-V4)
    vetoes_raw = _check_dict(record.get("vetoes", {}), "vetoes", set(rubric["vetoes"]))
    blocked_vetoes = []
    for vid in rubric["vetoes"]:
        vobj = _check_dict(vetoes_raw.get(vid, {}), f"vetoes.{vid}")
        st = vobj.get("status")
        if st not in (None, "通过", "阻断"):
            raise ValueError(f"vetoes.{vid}.status 须为 通过 / 阻断 / null。")
        ev_ok = _has_text(vobj.get("evidence", ""), f"vetoes.{vid}.evidence")
        if st == "阻断":
            blocked_vetoes.append({"id": vid, "evidence": vobj.get("evidence", "").strip()})
        if st is None or not ev_ok:
            pending.append(f"vetoes.{vid}")

    # 2. Signals (S1-S11)
    signals_raw = _check_dict(record.get("signals", {}), "signals", set(rubric["signals"]))
    s1_6_red = []
    s7_11_red = []
    s_mild = []
    for sid, sdef in rubric["signals"].items():
        sobj = _check_dict(signals_raw.get(sid, {}), f"signals.{sid}")
        lvl = sobj.get("level")
        if lvl is not None:
            if isinstance(lvl, bool) or not isinstance(lvl, int) or lvl not in (0, 1, 2):
                raise ValueError(f"signals.{sid}.level 须为 0 / 1 / 2 / null。")
        ev_ok = _has_text(sobj.get("evidence", ""), f"signals.{sid}.evidence")
        if lvl is None or (lvl > 0 and not ev_ok):
            pending.append(f"signals.{sid}")
        elif lvl == 2:
            if sdef["category"] == "process":
                s1_6_red.append({"id": sid, "title": sdef["title"], "evidence": sobj.get("evidence", "").strip()})
            else:
                s7_11_red.append({"id": sid, "title": sdef["title"], "evidence": sobj.get("evidence", "").strip()})
        elif lvl == 1:
            s_mild.append(sid)

    warnings = []
    if len(s1_6_red) >= 2 and not any(v["id"] == "V4" for v in blocked_vetoes):
        warnings.append("S1–S6 工序红灯已达 >=2 项，按规则应触发 V4 阻断，请复核 vetoes.V4！")

    # 3. Pages
    pages_list = record.get("pages", [])
    if not isinstance(pages_list, list):
        raise ValueError("pages 须为数组。")
    pages_by_idx = {}
    for p in pages_list:
        p = _check_dict(p, "pages[]")
        ri = p.get("review_index")
        if type(ri) is not int or not 1 <= ri <= n or ri in pages_by_idx:
            raise ValueError(f"pages.review_index 无效或重复：{ri!r}")
        pages_by_idx[ri] = p

    observations: dict[str, list] = {code: [] for code in (page_ids + overall_ids)}

    def check_item(entry, code: str, loc: str):
        entry = _check_dict(entry, loc)
        val = entry.get("score")
        cdef = criteria[code]
        if val == NA:
            if not cdef["allows_na"] or (cdef["na_rule"] == "SINGLE_PAGE" and total_slides != 1):
                raise ValueError(f"{loc} 不允许标为「不适用」。")
        elif isinstance(val, (int, Decimal)) and not isinstance(val, bool) and val in (0, 1, 2):
            val = int(val)
        elif val is not None:
            raise ValueError(f"{loc}.score 须为 0 / 1 / 2 / 允许的不适用 / null。")
        ev_ok = _has_text(entry.get("evidence", ""), f"{loc}.evidence")
        if val is None or not ev_ok:
            pending.append(loc)
            return None
        observations[code].append(val)
        return val

    page_results = []
    for ri in range(1, n + 1):
        p = pages_by_idx.get(ri, {})
        slide_no = p.get("slide_no", ri)
        loc = f"p{slide_no}(#{ri})"
        role_ok = _has_text(p.get("role", ""), f"{loc}.role")
        if not role_ok:
            pending.append(f"{loc}.role")
        items_obj = _check_dict(p.get("items", {}), f"{loc}.items", set(page_ids))
        ratings = {code: check_item(items_obj.get(code, {}), code, f"{loc}.{code}") for code in page_ids}
        nums = [v for v in ratings.values() if type(v) is int]
        complete = role_ok and None not in ratings.values()
        pscore = float(rounded(Fraction(50 * sum(nums), len(nums)), 1)) if complete and nums else None
        page_results.append({
            "review_index": ri,
            "slide_no": slide_no,
            "role": p.get("role", "").strip(),
            "score": pscore,
            "complete": complete,
            "weak_items": {k: v for k, v in ratings.items() if v in (0, 1)},
        })

    # 4. Overall items
    overall_raw = _check_dict(record.get("overall", {}), "overall", set(overall_ids))
    overall_ratings = {code: check_item(overall_raw.get(code, {}), code, f"overall.{code}") for code in overall_ids}

    zero_pages = [p["slide_no"] for p in page_results if 0 in p["weak_items"].values()]
    zero_overall = [code for code, v in overall_ratings.items() if v == 0]
    zero_page_limit = 0 if n <= 5 else max(1, int(n * 0.10))

    # 5. Aggregate dual-axis & A-H dimensions
    item_scores = {}
    app_w = 0
    tot_pts = Fraction(0)
    aai_w = 0
    aai_pts = Fraction(0)
    dim_w: dict[str, int] = {d: 0 for d in "ABCDEFGH"}
    dim_pts: dict[str, Fraction] = {d: Fraction(0) for d in "ABCDEFGH"}

    for code in page_ids + overall_ids:
        cdef = criteria[code]
        vals = observations[code]
        nums = [v for v in vals if type(v) is int]
        needed = n if cdef["scope"] == "逐页" else 1
        complete = len(vals) == needed
        mean = None
        if complete:
            mean = Fraction(sum(nums), len(nums)) if nums else NA
            if nums:
                w = cdef["weight"]
                pts = Fraction(w, 2) * mean
                app_w += w
                tot_pts += pts
                dim_w[cdef["dim"]] += w
                dim_pts[cdef["dim"]] += pts
                if cdef["anti_ai"]:
                    aai_w += w
                    aai_pts += pts
        item_scores[code] = {
            "title": cdef["title"],
            "weight": cdef["weight"],
            "anti_ai": cdef["anti_ai"],
            "score": float(rounded(mean, 2)) if isinstance(mean, Fraction) else mean,
            "rated": len(vals),
            "applicable": len(nums),
            "required": needed,
        }

    all_complete = len(pending) == 0
    total_score = float(rounded(100 * tot_pts / app_w, 2)) if all_complete and app_w > 0 else None
    anti_ai_score = float(rounded(100 * aai_pts / aai_w, 2)) if all_complete and aai_w > 0 else None
    dim_scores = {
        d: (float(rounded(100 * dim_pts[d] / dim_w[d], 1)) if all_complete and dim_w[d] > 0 else None)
        for d in "ABCDEFGH"
    }

    # 6. Tier & Target verdict (identical to Excel v2.2 formulas!)
    if blocked_vetoes:
        tier = "D 级｜阻断交付"
        target_met = "阻断未达标"
    elif not all_complete or total_score is None or anti_ai_score is None:
        tier = "待完成检查"
        target_met = "待完成"
    elif total_score < 55 or anti_ai_score < 40:
        tier = "C 级｜明显 AI 味/重做"
        target_met = "未达目标"
    elif (
        total_score >= 88
        and anti_ai_score >= 85
        and len(zero_overall) == 0
        and len(zero_pages) <= zero_page_limit
        and len(s1_6_red) + len(s7_11_red) == 0
    ):
        tier = "S 级｜优秀手工标杆"
        req_aai = 85 if float(target) >= 88 else min(float(target), 72)
        target_met = "达到修订目标" if (total_score >= float(target) and anti_ai_score >= req_aai) else "未达目标"
    elif total_score >= 75 and anti_ai_score >= 72 and len(s1_6_red) == 0 and len(s7_11_red) <= 1:
        tier = "A 级｜良好手工级"
        req_aai = 85 if float(target) >= 88 else min(float(target), 72)
        target_met = (
            "达到修订目标"
            if (total_score >= float(target) and anti_ai_score >= req_aai and len(zero_overall) == 0 and len(s1_6_red) + len(s7_11_red) == 0)
            else "未达目标"
        )
    else:
        tier = "B 级｜混合·短板/AI味"
        target_met = "未达目标"

    # 7. Prioritized Fix Queue for revision loop
    fix_queue = []
    for bv in blocked_vetoes:
        fix_queue.append({"priority": "P0-否决阻断", "target": bv["id"], "detail": bv["evidence"]})
    for sr in s1_6_red + s7_11_red:
        fix_queue.append({"priority": "P1-红灯指纹", "target": f"{sr['id']} {sr['title']}", "detail": sr["evidence"]})
    for code in zero_overall:
        fix_queue.append({"priority": "P2-整稿0分", "target": f"{code} {criteria[code]['title']}", "detail": overall_raw[code].get("evidence", "").strip()})
    for p in page_results:
        z_items = [k for k, v in p["weak_items"].items() if v == 0]
        if z_items:
            fix_queue.append({"priority": "P3-单页0分", "target": f"p{p['slide_no']} ({', '.join(z_items)})", "detail": p["role"]})
    # High-impact Anti-AI & weighted weak items (< 1.5)
    weak_scored = []
    for code, info in item_scores.items():
        if isinstance(info["score"], (int, float)) and info["score"] < 1.5 and code not in zero_overall:
            weak_scored.append((0 if info["anti_ai"] else 1, -info["weight"] * (2.0 - info["score"]), code, info))
    weak_scored.sort()
    for _, _, code, info in weak_scored[:8]:
        tag = "P4-去AI味短板" if info["anti_ai"] else "P5-高权提分项"
        fix_queue.append({"priority": tag, "target": f"{code} {info['title']} (均分 {info['score']}/2, {info['weight']}权)", "detail": ""})

    return {
        "rubric_version": rubric["version"],
        "deck": record.get("deck", ""),
        "genre": rubric["genres"][genre_key]["label"],
        "mode": record.get("mode", "full"),
        "total_slides": total_slides,
        "reviewed_pages": sum(1 for p in page_results if p["complete"]),
        "page_count": n,
        "target": float(target),
        "tier": tier,
        "target_met": target_met,
        "total_score": total_score,
        "anti_ai_score": anti_ai_score,
        "applicable_weight": app_w,
        "anti_ai_weight": aai_w,
        "dim_scores": dim_scores,
        "blocked_vetoes": [v["id"] for v in blocked_vetoes],
        "s1_6_red": [s["id"] for s in s1_6_red],
        "s7_11_red": [s["id"] for s in s7_11_red],
        "s_mild": s_mild,
        "zero_overall": zero_overall,
        "zero_pages": zero_pages,
        "zero_page_limit": zero_page_limit,
        "warnings": warnings,
        "pending_count": len(pending),
        "pending": pending[:20],
        "fix_queue": fix_queue,
        "item_scores": item_scores,
        "page_results": page_results,
    }


def format_score_summary(res: dict) -> str:
    lines = [
        f"=== PPT 质量评定报告（{res['rubric_version']}）：{res['deck']} ===",
        f"体裁：{res['genre']} | 模式：{res['mode']}（已核 {res['reviewed_pages']}/{res['page_count']} 页，全稿 {res['total_slides']} 页）",
        f"综合档位：{res['tier']} | 修订目标({res['target']})：{res['target_met']}",
        f"整体得分：{res['total_score']}（适用权 {res['applicable_weight']}）| 去 AI 味指数：{res['anti_ai_score']}（去AI权 {res['anti_ai_weight']}）",
        "八维分：" + " ".join(f"{k}={v}" for k, v in res["dim_scores"].items() if v is not None),
        f"否决阻断：{res['blocked_vetoes'] or '无'} | 工序红灯(S1-S6)：{res['s1_6_red'] or '无'} | AI味红灯(S7-S11)：{res['s7_11_red'] or '无'} | 整稿0分：{res['zero_overall'] or '无'} | 含0分页：{res['zero_pages'] or '无'} (上限{res['zero_page_limit']})",
    ]
    for w in res["warnings"]:
        lines.append(f"⚠ {w}")
    if res["pending_count"]:
        lines.append(f"待补核项（共 {res['pending_count']} 处，显示前 10）：{res['pending'][:10]}")
    if res["fix_queue"]:
        lines.append("优先修改队列：")
        for idx, fq in enumerate(res["fix_queue"][:10], 1):
            extra = f" — {fq['detail']}" if fq["detail"] else ""
            lines.append(f"  {idx}. [{fq['priority']}] {fq['target']}{extra}")
    return "\n".join(lines)


# =============================================================================
# SUBCOMMAND: diff
# =============================================================================
def diff_rounds(a: dict, b: dict) -> dict:
    def delta(x, y):
        if x is None or y is None:
            return None
        return round(y - x, 2)

    item_changes = []
    common = sorted(set(a["item_scores"]) & set(b["item_scores"]))
    for code in common:
        sa = a["item_scores"][code]["score"]
        sb = b["item_scores"][code]["score"]
        if isinstance(sa, (int, float)) and isinstance(sb, (int, float)) and abs(sb - sa) >= 0.05:
            item_changes.append({
                "id": code,
                "title": b["item_scores"][code]["title"],
                "before": sa,
                "after": sb,
                "delta": round(sb - sa, 2),
            })
    item_changes.sort(key=lambda x: -abs(x["delta"]))

    return {
        "before_deck": a["deck"],
        "after_deck": b["deck"],
        "tier": f"{a['tier']} -> {b['tier']}",
        "total_score": {"before": a["total_score"], "after": b["total_score"], "delta": delta(a["total_score"], b["total_score"])},
        "anti_ai_score": {"before": a["anti_ai_score"], "after": b["anti_ai_score"], "delta": delta(a["anti_ai_score"], b["anti_ai_score"])},
        "blocked_vetoes": {"before": a["blocked_vetoes"], "after": b["blocked_vetoes"]},
        "red_signals": {
            "before": a["s1_6_red"] + a["s7_11_red"],
            "after": b["s1_6_red"] + b["s7_11_red"],
        },
        "zero_overall": {"before": a["zero_overall"], "after": b["zero_overall"]},
        "zero_pages": {"before": a["zero_pages"], "after": b["zero_pages"]},
        "item_changes": item_changes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rubric", type=Path, default=RUBRIC_JSON, help="v2.2 量表 JSON 路径")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_guide = sub.add_parser("guide", help="输出 v2.2 评分锚点、否决项与指纹规则")
    p_guide.add_argument("--genre", help="按体裁过滤 H 项：01 / 02 / 03 / 04 / other")
    p_guide.add_argument("--format", choices=("md", "json"), default="md")

    p_scan = sub.add_parser("scan", help="无依赖解析 .pptx XML 预检 S1–S11、嵌入链接、占位符与复读")
    p_scan.add_argument("pptx", type=Path, help="待检 .pptx 文件路径")
    p_scan.add_argument("-o", "--output", type=Path, help="完整预检结果写入 JSON 文件")

    p_init = sub.add_parser("init", help="建立一轮 v2.2 空评审记录 JSON")
    p_init.add_argument("--deck", required=True, help="稿件名或版本号")
    p_init.add_argument("--genre", required=True, help="体裁：01 / 02 / 03 / 04 / other")
    p_init.add_argument("--pages", type=int, required=True, help="本次评审页数（全评或抽检页数）")
    p_init.add_argument("--total-slides", type=int, default=0, help="稿件总页数（默认等于 --pages）")
    p_init.add_argument("--mode", choices=("full", "sample"), default="full")
    p_init.add_argument("--slide-numbers", help="逗号分隔的原页号列表，如 1,2,5,10,15")
    p_init.add_argument("--target", type=Decimal, default=Decimal("88"))
    p_init.add_argument("-o", "--output", type=Path, help="输出 JSON 路径（默认打印）")

    p_score = sub.add_parser("score", help="校验并计算一轮评审记录的双轴得分与档位")
    p_score.add_argument("record", type=Path, help="评审记录 JSON 路径")
    p_score.add_argument("-o", "--output", type=Path, help="完整计分 JSON 输出路径")

    p_diff = sub.add_parser("diff", help="对比两轮评审记录的提分与修复变化")
    p_diff.add_argument("before", type=Path)
    p_diff.add_argument("after", type=Path)

    args = parser.parse_args()
    try:
        if args.cmd == "scan":
            res = scan_pptx(args.pptx)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(format_scan_summary(res))
            if args.output:
                print(f"完整预检 JSON 已写入：{args.output}")
            return 0

        rubric = load_rubric(args.rubric)

        if args.cmd == "guide":
            print(cmd_guide(rubric, args.genre, args.format))
        elif args.cmd == "init":
            gk = normalize_genre(args.genre)
            s_nums = [int(x.strip()) for x in args.slide_numbers.split(",") if x.strip()] if args.slide_numbers else None
            rec = new_record(rubric, gk, args.pages, args.deck, args.mode, args.total_slides or args.pages, s_nums, args.target)
            txt = json.dumps(rec, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(txt, encoding="utf-8")
                print(f"已创建 v2.2 评审模板：{args.output}（体裁 {rec['genre_label']}，{rec['page_count']} 页）")
            else:
                print(txt)
        elif args.cmd == "score":
            raw = json.loads(args.record.read_text(encoding="utf-8"), parse_float=Decimal)
            res = assess(raw, rubric)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(format_score_summary(res))
            if args.output:
                print(f"完整计分 JSON 已写入：{args.output}")
        elif args.cmd == "diff":
            ra = assess(json.loads(args.before.read_text(encoding="utf-8"), parse_float=Decimal), rubric)
            rb = assess(json.loads(args.after.read_text(encoding="utf-8"), parse_float=Decimal), rubric)
            print(json.dumps(diff_rounds(ra, rb), ensure_ascii=False, indent=2))
    except (ArithmeticError, BadZipFile, ET.ParseError, FileNotFoundError, IndexError, KeyError, OSError, ValueError) as err:
        print(f"rubric.py error: {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
