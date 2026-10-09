"""内容权限 / 分享核心。

模型：
- 每个资源有 owner（kb/file/folder/customer 用 owner_id，notebook 用 created_by）
- is_private：TRUE=私有（仅 owner + 被分享者）；NULL/FALSE=团队共享
  （**团队语义**：仅 owner 所在团队（users.group_id）成员可见；group_id 为空的
  个人用户不参与任何团队共享——既看不到别人的共享内容，自己的共享内容别人也看不到。
  文档/知识库/工作区共享=只读；客户=协作型资源，共享=同团队可编辑）
- resource_permissions ACL：把资源分享给指定用户，权限 read/edit/owner
- 管理员（role=admin）绕过所有 ACL
- customer 无文件夹继承，走通用直接权限；列表类查询用 customer_visible_clause 做 SQL 级过滤

文件夹权限管理标准（级联继承，类网盘）：
- 文件夹是"访问容器"：能访问某文件夹 → 其全部子文件夹及其中文件自动可见。
- 有效权限 = 资源自身权限 与 所有祖先文件夹权限中的最高者（read < edit < owner）；
  单独给某子文件夹/文件更高的 read/edit/owner 可覆盖提升。
- 管理纪律：敏感文件勿放进已分享的文件夹；需要单独保护的条目请移出分享区。
- 删除/重命名等写操作仍要求对应节点的 owner。
"""
import logging

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_base import KnowledgeBase
from app.models.customer import Customer
from app.models.library_file import LibraryFile
from app.models.library_folder import LibraryFolder
from app.models.notebook import Notebook
from app.models.resource_permission import ResourcePermission
from app.models.user import User

logger = logging.getLogger(__name__)

_MODELS = {
    "kb": KnowledgeBase, "file": LibraryFile, "folder": LibraryFolder,
    "notebook": Notebook, "customer": Customer,
}
_OWNER_FIELD = {
    "kb": "owner_id", "file": "owner_id", "folder": "owner_id",
    "notebook": "created_by", "customer": "owner_id",
}

_LEVEL = {"none": 0, "read": 1, "edit": 2, "owner": 3}

# 团队可见时的默认权限：文档/知识库/工作区为只读（read）；
# 客户是协作型资源，团队共享保持历史行为=全员可编辑（edit），私有后按 ACL 收缩。
_TEAM_PERM = {"customer": "edit"}


def level(p: str | None) -> int:
    return _LEVEL.get(p or "none", 0)


def satisfies(perm: str | None, required: str) -> bool:
    return level(perm) >= level(required)


def is_team_visible(is_private: bool | None) -> bool:
    """NULL（存量数据）与 False 都视为团队共享。"""
    return is_private is None or not is_private


async def _user_group_ids(db: AsyncSession, user_ids: set[int]) -> dict[int, int | None]:
    """批量取用户 group_id（团队共享判定的 owner/查询者团队比对用）。"""
    if not user_ids:
        return {}
    rows = (
        await db.execute(select(User.id, User.group_id).where(User.id.in_(user_ids)))
    ).all()
    return {r.id: r.group_id for r in rows}


def _same_team(viewer_gid: int | None, owner_gid: int | None) -> bool:
    """同团队判定：双方 group_id 相同且非空（NULL ≠ NULL，个人用户不参与团队共享）。"""
    return viewer_gid is not None and owner_gid is not None and viewer_gid == owner_gid


async def _folder_graph(
    db: AsyncSession, tenant_id: int
) -> tuple[dict[int, int | None], dict[int | None, list[int]]]:
    """租户内未删除文件夹的 parent 映射与 children 映射。"""
    rows = (
        await db.execute(
            select(LibraryFolder.id, LibraryFolder.parent_id).where(
                LibraryFolder.tenant_id == tenant_id,
                LibraryFolder.deleted_at.is_(None),
            )
        )
    ).all()
    parent: dict[int, int | None] = {r.id: r.parent_id for r in rows}
    children: dict[int | None, list[int]] = {}
    for fid, pid in parent.items():
        children.setdefault(pid, []).append(fid)
    return parent, children


def _ancestors(parent_map: dict[int, int | None], fid: int) -> list[int]:
    """fid 的祖先文件夹 id 链（不含自身），防环。纯函数。"""
    chain: list[int] = []
    seen: set[int] = set()
    cur = parent_map.get(fid)
    while cur is not None and cur not in seen and cur in parent_map:
        chain.append(cur)
        seen.add(cur)
        cur = parent_map.get(cur)
    return chain


def _descendants(
    children_map: dict[int | None, list[int]], seed: set[int]
) -> set[int]:
    """seed 的全部后代文件夹 id（含 seed 自身，向下递归）。纯函数。"""
    out = set(seed)
    stack = list(seed)
    while stack:
        cur = stack.pop()
        for c in children_map.get(cur, []):
            if c not in out:
                out.add(c)
                stack.append(c)
    return out


async def _get_direct_access(
    db: AsyncSession, tenant_id: int, user_id: int, rtype: str, obj
) -> str | None:
    """单个资源上的直接权限（不含祖先文件夹继承）：owner > ACL > 团队共享 > None。

    团队共享仅对 owner 同团队成员生效（双方 group_id 相同且非空）。"""
    owner_id = getattr(obj, _OWNER_FIELD[rtype])
    if owner_id == user_id:
        return "owner"
    perm: str | None = None
    if is_team_visible(obj.is_private):
        gids = await _user_group_ids(db, {user_id, owner_id})
        if _same_team(gids.get(user_id), gids.get(owner_id)):
            perm = _TEAM_PERM.get(rtype, "read")
    acl = await db.scalar(
        select(ResourcePermission.permission).where(
            ResourcePermission.tenant_id == tenant_id,
            ResourcePermission.resource_type == rtype,
            ResourcePermission.resource_id == obj.id,
            ResourcePermission.user_id == user_id,
        )
    )
    if acl and level(acl) > level(perm):
        perm = acl
    return perm


async def get_access_for(
    db: AsyncSession, tenant_id: int, user_id: int, rtype: str, obj
) -> str | None:
    """对已取出的对象计算权限（避免重复 db.get）。

    仅适用于无文件夹继承的资源类型（kb/notebook/customer）；folder/file 请用 get_access。"""
    if obj is None or obj.tenant_id != tenant_id:
        return None
    return await _get_direct_access(db, tenant_id, user_id, rtype, obj)


async def get_access(
    db: AsyncSession, tenant_id: int, user_id: int, rtype: str, rid: int
) -> str | None:
    """单个资源上用户的权限：owner > ACL > 团队共享 > None。

    对 folder/file 还会向上继承祖先文件夹权限（取链上最高）。"""
    model = _MODELS[rtype]
    obj = await db.get(model, rid)
    if obj is None or obj.tenant_id != tenant_id:
        return None
    direct = await _get_direct_access(db, tenant_id, user_id, rtype, obj)
    if rtype not in ("folder", "file"):
        return direct
    parent_map, _ = await _folder_graph(db, tenant_id)
    if rtype == "folder":
        chain = [rid] + _ancestors(parent_map, rid)
    else:
        fid = getattr(obj, "folder_id", None)
        chain = ([fid] if fid else []) + (_ancestors(parent_map, fid) if fid else [])
    best = direct
    for cf in chain:
        fobj = await db.get(LibraryFolder, cf)
        if fobj is None or fobj.tenant_id != tenant_id or fobj.deleted_at is not None:
            continue
        p = await _get_direct_access(db, tenant_id, user_id, "folder", fobj)
        if p and level(p) > level(best):
            best = p
    return best


async def _direct_perms(
    db: AsyncSession, user: User, rtype: str, ids: list[int]
) -> dict[int, str]:
    """批量解析直接权限（不含祖先继承）。管理员视为 owner。

    团队共享仅对 owner 同团队成员生效（owner group 一次批量预取）。"""
    if not ids:
        return {}
    model = _MODELS[rtype]
    tenant_id = user.tenant_id
    if user.role == "admin":
        return {i: "owner" for i in ids}
    rows = (
        await db.execute(
            select(model).where(model.id.in_(ids), model.tenant_id == tenant_id)
        )
    ).scalars().all()
    owner_field = _OWNER_FIELD[rtype]
    shared_rows = [
        r for r in rows
        if getattr(r, owner_field) != user.id and is_team_visible(r.is_private)
    ]
    owner_gids = (
        await _user_group_ids(db, {getattr(r, owner_field) for r in shared_rows})
        if shared_rows
        else {}
    )
    result: dict[int, str] = {}
    for r in rows:
        oid = getattr(r, owner_field)
        if oid == user.id:
            result[r.id] = "owner"
        elif is_team_visible(r.is_private) and _same_team(
            getattr(user, "group_id", None), owner_gids.get(oid)
        ):
            result[r.id] = _TEAM_PERM.get(rtype, "read")
    acl_rows = (
        await db.execute(
            select(ResourcePermission).where(
                ResourcePermission.tenant_id == tenant_id,
                ResourcePermission.resource_type == rtype,
                ResourcePermission.resource_id.in_(ids),
                ResourcePermission.user_id == user.id,
            )
        )
    ).scalars().all()
    for a in acl_rows:
        if level(a.permission) > level(result.get(a.resource_id)):
            result[a.resource_id] = a.permission
    return result


async def resolve_permissions(
    db: AsyncSession, user: User, rtype: str, ids: list[int]
) -> dict[int, str]:
    """批量解析权限（列表端点用），返回 {resource_id: perm}。管理员视为 owner。

    folder/file 取"自身 ∪ 祖先文件夹"中的最高权限（向下继承）。"""
    if not ids:
        return {}
    direct = await _direct_perms(db, user, rtype, ids)
    if rtype not in ("folder", "file"):
        return direct
    parent_map, _ = await _folder_graph(db, user.tenant_id)
    # 收集链上文件夹 id
    chain_folder_ids: set[int] = set()
    file_folder: dict[int, int | None] = {}
    if rtype == "folder":
        for rid in ids:
            chain_folder_ids.add(rid)
            chain_folder_ids.update(_ancestors(parent_map, rid))
    else:
        rows = (
            await db.execute(
                select(LibraryFile.id, LibraryFile.folder_id).where(
                    LibraryFile.id.in_(ids),
                    LibraryFile.tenant_id == user.tenant_id,
                )
            )
        ).all()
        file_folder = {r.id: r.folder_id for r in rows}
        for fid in file_folder.values():
            if fid:
                chain_folder_ids.add(fid)
                chain_folder_ids.update(_ancestors(parent_map, fid))
    folder_direct = (
        await _direct_perms(db, user, "folder", list(chain_folder_ids))
        if chain_folder_ids
        else {}
    )
    result = dict(direct)
    for rid in ids:
        if rtype == "folder":
            chain = [rid] + _ancestors(parent_map, rid)
        else:
            fid = file_folder.get(rid)
            chain = ([fid] if fid else []) + (_ancestors(parent_map, fid) if fid else [])
        best = direct.get(rid)
        for cf in chain:
            p = folder_direct.get(cf)
            if p and level(p) > level(best):
                best = p
        if best is not None:
            result[rid] = best
    return result


async def filter_accessible_ids(
    db: AsyncSession, user: User, rtype: str, ids: list[int]
) -> list[int]:
    """返回 ids 中当前用户可读的子集（RAG 传入的 kb_ids/file_ids 需要过滤）。"""
    if not ids:
        return []
    if user.role == "admin":
        return list(ids)
    perms = await resolve_permissions(db, user, rtype, ids)
    return [i for i in ids if i in perms]


async def accessible_ids(
    db: AsyncSession, user: User, rtype: str
) -> list[int]:
    """用户可见的资源 id：owner ∪ 同团队共享 ∪ ACL 被分享。管理员返回全部。

    folder：向下递归继承（可见文件夹的所有后代文件夹均可见）；
    file：自身可访问 ∪ 所在文件夹链触及可见文件夹的文件（级联继承）。
    """
    model = _MODELS[rtype]
    tenant_id = user.tenant_id
    if user.role == "admin":
        rows = (
            await db.execute(select(model.id).where(model.tenant_id == tenant_id))
        ).scalars().all()
        return list(rows)
    owner_field = getattr(model, _OWNER_FIELD[rtype])
    conds = [owner_field == user.id]
    user_gid = getattr(user, "group_id", None)
    if user_gid is not None:
        # 团队共享：owner 是同团队成员（group_id 相同且非空）的非私有资源
        team_ids = select(User.id).where(
            User.tenant_id == tenant_id, User.group_id == user_gid
        )
        conds.append(
            and_(
                or_(model.is_private.is_(None), model.is_private.is_(False)),
                owner_field.in_(team_ids),
            )
        )
    rows = (
        await db.execute(
            select(model.id).where(
                model.tenant_id == tenant_id,
                or_(*conds),
            )
        )
    ).scalars().all()
    ids = set(rows)
    acl = (
        await db.execute(
            select(ResourcePermission.resource_id).where(
                ResourcePermission.tenant_id == tenant_id,
                ResourcePermission.resource_type == rtype,
                ResourcePermission.user_id == user.id,
            )
        )
    ).scalars().all()
    ids.update(acl)

    if rtype == "folder":
        # 向下继承：可见文件夹的全部后代文件夹也可见
        _, children = await _folder_graph(db, tenant_id)
        ids = _descendants(children, ids)
        return sorted(ids)
    if rtype == "file":
        # 级联继承：文件所在文件夹链触及"可见文件夹"则可见
        visible_folders = set(await accessible_ids(db, user, "folder"))
        parent_map, _ = await _folder_graph(db, tenant_id)
        file_rows = (
            await db.execute(
                select(LibraryFile.id, LibraryFile.folder_id).where(
                    LibraryFile.tenant_id == tenant_id,
                    LibraryFile.deleted_at.is_(None),
                )
            )
        ).all()
        for fid, folder_id in file_rows:
            if fid in ids:
                continue
            chain = ([folder_id] if folder_id else []) + (
                _ancestors(parent_map, folder_id) if folder_id else []
            )
            if any(cf in visible_folders for cf in chain):
                ids.add(fid)
        return sorted(ids)
    return sorted(ids)


async def ensure_access(
    db: AsyncSession, user: User, rtype: str, rid: int, required: str = "read"
) -> None:
    """校验当前用户对资源的权限，不足抛 403。管理员放行。"""
    if user.role == "admin":
        return
    perm = await get_access(db, user.tenant_id, user.id, rtype, rid)
    if not satisfies(perm, required):
        raise HTTPException(status_code=403, detail="没有该资源的访问权限")


async def ensure_owner(
    db: AsyncSession, user: User, rtype: str, rid: int
) -> None:
    """仅 owner 或管理员可管理分享/删除。"""
    if user.role == "admin":
        return
    perm = await get_access(db, user.tenant_id, user.id, rtype, rid)
    if perm != "owner":
        raise HTTPException(status_code=403, detail="仅资源所有者可执行此操作")


def customer_visible_clause(user: User):
    """客户可见性 SQL 过滤：管理员返回 None（不过滤）；
    普通用户 = 同团队共享（is_private NULL/FALSE 且 owner 同团队）∪ 我负责 ∪ 被分享给我；
    个人用户（无团队）= 我负责 ∪ 被分享给我。"""
    if user.role == "admin":
        return None
    conds = [
        Customer.owner_id == user.id,
        Customer.id.in_(
            select(ResourcePermission.resource_id).where(
                ResourcePermission.tenant_id == user.tenant_id,
                ResourcePermission.resource_type == "customer",
                ResourcePermission.user_id == user.id,
            )
        ),
    ]
    user_gid = getattr(user, "group_id", None)
    if user_gid is not None:
        team_ids = select(User.id).where(
            User.tenant_id == user.tenant_id, User.group_id == user_gid
        )
        conds.append(
            and_(
                or_(Customer.is_private.is_(None), Customer.is_private.is_(False)),
                Customer.owner_id.in_(team_ids),
            )
        )
    return or_(*conds)
