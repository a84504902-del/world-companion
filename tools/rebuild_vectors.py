"""向量重建工具（换 embedding 模型后必须跑）。

为什么需要：
向量是「用某个模型算出来的」。换了模型，新旧向量来自不同的语义空间，
混在一起比较会得到毫无意义的结果——不报错，只是悄悄变差。
所以换模型后必须把旧向量全部丢掉、用新模型重算。

用法：
    python tools/rebuild_vectors.py            # 重建全部
    python tools/rebuild_vectors.py --dry-run  # 只看会重建什么，不动数据
    python tools/rebuild_vectors.py --check    # 只体检：中文识别率 + 维度是否一致

注意：重建会清空 memory_embeddings 和 chunk_embeddings 两张表的内容，
对应的记忆/原文块本身（memories / conversation_chunks）不动，只是重算向量。
运行前会自动备份 memory.db。
"""
import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def backup_db():
    """备份数据库（VACUUM INTO，热备份不需要停服务）"""
    import db
    src = db.DB_PATH if hasattr(db, "DB_PATH") else os.path.join(BASE, "memory.db")
    ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BASE, "backups", f"memory-pre-vector-rebuild-{ts}.db")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(src)
    conn.execute("VACUUM INTO ?", (dst,))
    conn.close()
    return dst


def check():
    """体检：当前模型认不认中文、维度是多少、库里向量维度对不对得上"""
    import embedding
    m = embedding.load_model()
    if m is None:
        print("✗ 模型加载失败，先解决模型问题")
        return 1

    info = embedding.model_info()
    print("=== 当前模型 ===")
    print(f"  路径: {info['path']}")
    print(f"  维度: {info['dim']}")

    print()
    print("=== 中文识别能力 ===")
    sup = embedding.check_chinese_support()
    if sup:
        print(f"  token 总数: {sup['total_tokens']}  [UNK]: {sup['unk_tokens']}  "
              f"丢失率: {sup['unk_ratio']:.1%}")
        print(f"  结论: {sup['verdict']}")
        if sup["unk_ratio"] >= 0.4:
            print()
            print("  ⚠️ 这个模型基本不认中文，记忆检索会静默失准。")
            print("     建议换中文模型：把 bge-small-zh-v1.5 放到 E:\\bus\\models\\embedding-zh")
            return 1
    else:
        print("  （无法检测）")

    print()
    print("=== 库中已有向量维度 ===")
    import db
    conn = db.connect()
    for table, col in (("memory_embeddings", "embedding"), ("chunk_embeddings", "embedding")):
        rows = conn.execute(f"SELECT {col} FROM {table} LIMIT 200").fetchall()
        if not rows:
            print(f"  {table}: 空")
            continue
        dims = set()
        for r in rows:
            try:
                import json
                dims.add(len(json.loads(r[col])))
            except Exception:
                pass
        mark = "✓ 一致" if dims == {info["dim"]} else "✗ 与当前模型不符，必须重建"
        print(f"  {table}: {len(rows)} 条，维度 {sorted(dims)} vs 当前模型 {info['dim']} → {mark}")

    print()
    print("下一步：python tools/rebuild_vectors.py")
    return 0


def rebuild(dry_run=False):
    import db
    import embedding
    import memory_retriever as mr

    m = embedding.load_model()
    if m is None:
        print("✗ 模型加载失败，先解决模型问题")
        return 1

    info = embedding.model_info()
    print(f"使用模型: {info['path']}（{info['dim']} 维）")

    sup = embedding.check_chinese_support()
    if sup and sup["unk_ratio"] >= 0.4:
        print(f"⚠️ 警告：该模型中文丢失率 {sup['unk_ratio']:.1%}，重建出来的向量质量会很差。")
        if not dry_run:
            ans = input("仍要继续吗？输入 y 确认: ").strip().lower()
            if ans != "y":
                print("已取消")
                return 1

    conn = db.connect()
    mem_roots = conn.execute("SELECT id, content FROM memories").fetchall()
    chunk_roots = conn.execute("SELECT id, content FROM conversation_chunks").fetchall()
    mem_emb = conn.execute("SELECT COUNT(*) c FROM memory_embeddings").fetchone()["c"]
    chunk_emb = conn.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]

    print()
    print("=== 现状 ===")
    print(f"  记忆 {len(mem_roots)} 条（向量 {mem_emb} 条）")
    print(f"  原文块 {len(chunk_roots)} 条（向量 {chunk_emb} 条）")

    if dry_run:
        print()
        print("这是 --dry-run，没有改动任何数据。去掉它就会真的重建。")
        return 0

    if not mem_emb and not chunk_emb:
        print()
        print("没有旧向量需要清理，无需重建。")
        return 0

    print()
    print("=== 备份数据库 ===")
    bpath = backup_db()
    print(f"  已备份: {bpath}")

    print()
    print("=== 清空旧向量 ===")
    conn.execute("DELETE FROM memory_embeddings")
    conn.execute("DELETE FROM chunk_embeddings")
    conn.commit()

    print()
    print("=== 用新模型重算 ===")
    ok = fail = 0
    for r in mem_roots:
        vec = embedding.embed_text(r["content"])
        if vec:
            db.save_memory_embedding(r["id"], embedding.vector_to_json(vec), r["content"])
            ok += 1
        else:
            fail += 1
    print(f"  记忆向量: 成功 {ok} 条，失败 {fail} 条")

    ok2 = fail2 = 0
    for r in chunk_roots:
        vec = embedding.embed_text(r["content"])
        if vec:
            db.save_chunk_embedding(r["id"], embedding.vector_to_json(vec), r["content"])
            ok2 += 1
        else:
            fail2 += 1
    print(f"  原文块向量: 成功 {ok2} 条，失败 {fail2} 条")

    # 重建内存缓存，否则本次运行仍用旧向量
    mr.clear_all_cache()
    mr.preload_cache()

    print()
    print("=== 完成 ===")
    print(f"  记忆向量 {ok} 条 / 原文块向量 {ok2} 条已用新模型重算")
    if fail or fail2:
        print(f"  有 {fail + fail2} 条向量化失败（模型返回空），这些条不会参与检索")
    print("  内存缓存已刷新，重启服务后生效")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只看会重建什么，不动数据")
    ap.add_argument("--check", action="store_true", help="只体检：中文识别率 + 维度一致性")
    args = ap.parse_args()

    if args.check:
        sys.exit(check())
    sys.exit(rebuild(args.dry_run))