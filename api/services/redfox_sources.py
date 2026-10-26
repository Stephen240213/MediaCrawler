"""Read expressions shared by single-content and batch analysis."""
from database.analytics import timestamp


def expressions(db, alias='c'):
    cols = {row[1] for row in db.execute('PRAGMA table_info(xhs_note)')}
    has_source = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='redfox_sources' AND type='table'").fetchone())
    def native(column, default="''"):
        return f'(SELECT n."{column}" FROM xhs_note n WHERE n.note_id={alias}.content_id LIMIT 1)' if {'note_id', column} <= cols else default
    body = f'coalesce({native("desc")},\'\')'
    creator = f'coalesce({native("creator_hash")},\'\')'
    user = f'coalesce({native("user_id")},\'\')'
    if has_source:
        def remote(column):
            return f'(SELECT r.{column} FROM redfox_sources r WHERE r.platform={alias}.platform AND r.content_id={alias}.content_id)'
        creator = f'coalesce(nullif({creator},\'\'),{remote("creator_hash")},\'\')'
        db.create_function('mc_redfox_time', 1, timestamp, deterministic=True)
        observed = f'coalesce(mc_redfox_time({native("last_modify_ts")}),\'\')'
        body = f'''CASE WHEN coalesce({remote('body')},'')!='' AND
            (coalesce({remote('observed_at')},'')>={observed} OR {body}='')
            THEN {remote('body')} ELSE {body} END'''
    return dict(body=body, creator=creator, user=user)
