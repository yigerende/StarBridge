"""Shared validation and stable ordering for paginated read models."""
from .common import require

PAGE_SIZES = (10, 50, 100, 500)


def order_by(field, direction, columns, default, stable='id'):
    """Only application-owned SQL expressions may enter ORDER BY."""
    field = field or default
    require(field in columns, '排序字段无效')
    require(direction in ('asc', 'desc'), '排序方向无效')
    expression = columns[field]
    tie=', '.join(f'{key.strip()} {direction.upper()}' for key in stable.split(','))
    return f'{expression} {direction.upper()} NULLS LAST, {tie}'


def pagination(page, size):
    require(isinstance(page, int) and 1 <= page <= 1000000, '页码无效')
    require(isinstance(size, int) and 1 <= size <= 500, '每页条数须在 1～500 之间')


def result(items, total, page, size, **extra):
    return dict(items=items, total=total, page=page, page_size=size,
                has_more=page * size < total, **extra)


def page_rows(rows, *, page=1, page_size=10, q='', sort_by='', sort_dir='asc', columns=None, search=None, default='id'):
    """Paginate finite configuration records on the server, before transmission."""
    pagination(page, page_size)
    require(isinstance(q, str) and len(q) <= 200, '搜索内容不能超过 200 个字符')
    columns = columns or {'id': lambda row: row.get('id')}
    field = sort_by or default
    require(field in columns and sort_dir in ('asc', 'desc'), '排序参数无效')
    if q:
        needle = q.strip().casefold()
        rows = [r for r in rows if needle in str(search(r) if search else r.get('name', '')).casefold()]
    key = columns[field]
    present = [r for r in rows if key(r) is not None]
    missing = [r for r in rows if key(r) is None]
    present.sort(key=lambda row: (key(row), str(row.get('id', row.get('code', '')))), reverse=sort_dir == 'desc')
    rows = present + missing
    total = len(rows)
    page = min(page, max(1, (total + page_size - 1) // page_size))
    return result(rows[(page - 1) * page_size:page * page_size], total, page, page_size)
