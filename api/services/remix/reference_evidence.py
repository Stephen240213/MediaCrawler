"""Import frozen upstream evidence into a separate, bounded citation namespace."""
import json
import re

from .providers import AnalysisError

VERSION = 'upstream-citations-v2-complete'
MAX_ITEMS = 5000
FACT_KINDS = {'title', 'body', 'transcript', 'supplement'}


def factual(entry):
    return entry.get('kind') in FACT_KINDS or entry.get('kind')=='source_excerpt' and bool(entry.get('factual_source'))


def prepare(source):
    if source.get('kind') not in ('report', 'research'):
        return source, []
    bundle = source.get('reference_bundle')
    if bundle is None:
        # Old failed jobs keep their original frozen snapshot, never fetch a newer report.
        try:
            bundle = json.loads(source.get('body', ''))
        except (TypeError, ValueError):
            raise AnalysisError('创作来源快照不完整，无法恢复引用。请重新选择原报告或研究版本新建创作；尚未调用模型。') from None
    if not isinstance(bundle, dict) or not isinstance(bundle.get('evidence'), list):
        raise AnalysisError('创作来源缺少原文引用，请重新选择报告或研究版本；尚未调用模型。')
    originals = list(bundle['evidence'])
    original_source=bundle.get('source_data') or {}
    original_body=original_source.get('body','') if original_source.get('kind') not in ('report','research','analytics') else ''
    if original_body and ''.join(e.get('text','') for e in originals if e.get('kind')=='body')!=original_body:
        originals.append(dict(id='raw:body',kind='body',text=original_body,truncated=original_source.get('truncated',False)))
    known_texts={e.get('text') for e in originals}
    for i,segment in enumerate(bundle.get('transcript',[])):
        if segment.get('text') and segment['text'] not in known_texts:
            originals.append(dict(id=f'raw:transcript:{i}',kind='transcript',text=segment['text']))
    valid = [e for e in originals if isinstance(e, dict) and isinstance(e.get('id'), str)
             and isinstance(e.get('text'), str) and e['text'].strip()]
    if not valid:
        raise AnalysisError('创作来源没有可用的原文引用，请重新选择报告或研究版本；尚未调用模型。')
    if len({e['id'] for e in valid}) != len(valid):
        raise AnalysisError('创作来源有重复引用编号，请核对原报告；尚未调用模型。')
    parent_kind=bundle.get('source_kind')
    if parent_kind in ('report','research'):
        valid=[dict(e,kind='report_context') if e.get('kind')=='body' else e for e in valid]
    # Prefer original facts before report settings or intermediate model context.
    if len(valid)>MAX_ITEMS:
        raise AnalysisError('完整上游证据超过5000条，请缩小来源范围；尚未调用模型，未静默丢弃证据。')
    selected = sorted(valid, key=lambda e: not factual(e))
    mapping = {e['id']: f'R{i:04d}' for i, e in enumerate(selected, 1)}
    all_ids = {e['id'] for e in valid}
    pattern = re.compile(r'(?<![\w-])(?:'+ '|'.join(re.escape(v) for v in sorted(all_ids, key=len, reverse=True))+r')(?![\w-])') if all_ids else None

    def text(value):
        return pattern.sub(lambda m: mapping.get(m[0], '未纳入的上游引用'), value) if pattern else value

    def remap(value):
        if isinstance(value, dict):
            return {k: [mapping[ref] for ref in v if isinstance(ref,str) and ref in mapping] if k in ('evidence_ids', 'summary_evidence_ids') and isinstance(v, list)
                    else remap(v) for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v) for v in value]
        return text(value) if isinstance(value, str) else value

    imported = []
    for i, e in enumerate(selected):
        # Preserve original facts/comments verbatim. Derived report context can contain
        # parent citation labels and follows the same input map as the narrative.
        quoted=factual(e) or e.get('origin_kind',e.get('kind'))=='comment'
        normalized = e['text'] if quoted else text(e['text'])
        excerpt = normalized
        if not excerpt:
            continue
        imported.append(dict(id=mapping[e['id']], kind='source_excerpt', text=excerpt,
            origin_kind=e.get('origin_kind', e.get('kind', 'unknown')), upstream_id=e['id'],
            factual_source=factual(e),
            truncated=len(excerpt)<len(normalized) or bool(e.get('truncated'))))
    # The model receives the report narrative once, alongside the actual source excerpts.
    # Old citation strings are replaced only via the known input map, never on model output.
    narrative = {k: v for k, v in bundle.items() if k not in ('evidence','source_kind','source_data','transcript')}
    body = json.dumps(remap(narrative), ensure_ascii=False)
    prepared = dict(source, body=body, original_body_chars=len(body), truncated=False,
        upstream_coverage=dict(version=VERSION, original=len(originals), included=len(imported),
            excerpt_chars=sum(len(e['text']) for e in imported), omitted=len(originals)-len(imported)))
    return prepared, imported
