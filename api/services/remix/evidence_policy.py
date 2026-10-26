"""Evidence capabilities are assigned by the application, never by model prose."""
import json


def capabilities(entry):
    kind=entry.get('kind');origin=entry.get('origin_kind')
    if kind=='comment' or kind=='source_excerpt' and origin=='comment':
        return {'comment_text'}
    if kind=='comment_topics' or kind=='source_excerpt' and origin=='comment_topics':
        try:data=json.loads(entry['text'])
        except (ValueError,TypeError,KeyError):return set()
        if isinstance(data,dict) and type(data.get('sample_size')) is int and isinstance(data.get('groups'),list):
            return {'comment_statistics'}
        return set()
    if kind=='data_analysis' or kind=='source_excerpt' and origin=='data_analysis':
        try:data=json.loads(entry['text'])
        except (ValueError,TypeError,KeyError):return set()
        if not isinstance(data,dict):return set()
        result=set()
        summary=data.get('summary',{})
        if isinstance(summary,dict) and type(summary.get('content_count')) is int:
            result.add('content_statistics')
        comments=data.get('comment_analysis')
        if isinstance(comments,dict) and type(comments.get('total_comments')) is int and isinstance(comments.get('themes'),list):
            result.add('comment_statistics')
        return result
    if kind in ('title','body','transcript') or kind=='source_excerpt' and entry.get('factual_source'):
        return {'content_text'}
    if kind=='supplement':return {'user_material'}
    if kind in ('report_context','project_research'):return {'analysis_result'}
    return {'writing_context'}


def index(evidence):
    return {entry['id']:capabilities(entry) for entry in evidence}


def comment_basis(ids,roles):
    supported={'comment_text','comment_statistics'}
    if any(not roles.get(identifier,set())&supported for identifier in ids):return None
    used=set().union(*(roles[identifier]&supported for identifier in ids))
    if used=={'comment_statistics'}:return 'comment_statistics'
    if used=={'comment_text'}:return 'raw_comments'
    return 'mixed'


def factual_ids(evidence):
    accepted={'content_text','user_material','content_statistics','comment_statistics'}
    return {entry['id'] for entry in evidence if capabilities(entry)&accepted}
