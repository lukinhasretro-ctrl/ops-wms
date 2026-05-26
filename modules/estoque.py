from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
import csv, io
from database import db, PH, like, rows, row
from helpers import normalizar_data, enriquecer_lote, dias_vencer

router = APIRouter(prefix="/api/estoque", tags=["estoque"])

class LoteIn(BaseModel):
    sku:        str
    descricao:  Optional[str] = ""
    lote:       str
    validade:   str
    quantidade: float
    unidade:    Optional[str] = "UN"
    endereco:   str
    fornecedor: Optional[str] = ""

class MovIn(BaseModel):
    tipo:       str
    quantidade: float
    usuario:    Optional[str] = "operador"
    obs:        Optional[str] = ""
    pedido_ref: Optional[str] = ""

@router.get("/lotes")
def listar(sku: str = "", endereco: str = "", status: str = ""):
    with db() as con:
        cur = con.cursor()
        q = "SELECT * FROM lotes WHERE ativo=1"
        p = []
        if sku:
            q += f" AND (sku {like()} {PH} OR descricao {like()} {PH})"
            p += [f"%{sku}%", f"%{sku}%"]
        if endereco:
            q += f" AND endereco {like()} {PH}"; p.append(f"%{endereco}%")
        q += " ORDER BY validade ASC"
        cur.execute(q, p)
        dados = [enriquecer_lote(r) for r in rows(cur)]
    if status:
        dados = [d for d in dados if d["status_val"] == status]
    return dados

@router.post("/lotes", status_code=201)
def criar(l: LoteIn):
    with db() as con:
        cur = con.cursor()
        from database import DATABASE_URL
        if DATABASE_URL:
            cur.execute(f"""INSERT INTO lotes
                (sku,descricao,lote,validade,quantidade,unidade,endereco,fornecedor)
                VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH}) RETURNING id""",
                (l.sku.upper(), l.descricao, l.lote.upper(), l.validade,
                 l.quantidade, l.unidade, l.endereco.upper(), l.fornecedor))
            lid = cur.fetchone()["id"]
        else:
            cur.execute(f"""INSERT INTO lotes
                (sku,descricao,lote,validade,quantidade,unidade,endereco,fornecedor)
                VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH})""",
                (l.sku.upper(), l.descricao, l.lote.upper(), l.validade,
                 l.quantidade, l.unidade, l.endereco.upper(), l.fornecedor))
            lid = cur.lastrowid
        cur.execute(f"INSERT INTO movimentos (lote_id,tipo,quantidade,usuario,obs) VALUES ({PH},'ENTRADA',{PH},'recebimento',{PH})",
                    (lid, l.quantidade, f"Recebimento — {l.fornecedor}"))
        cur.execute(f"SELECT * FROM lotes WHERE id={PH}", (lid,))
        return enriquecer_lote(row(cur.fetchone()))

@router.delete("/lotes/{lid}")
def remover(lid: int):
    with db() as con:
        con.cursor().execute(f"UPDATE lotes SET ativo=0 WHERE id={PH}", (lid,))
    return {"ok": True}

@router.post("/lotes/{lid}/mov")
def movimentar(lid: int, m: MovIn):
    with db() as con:
        cur = con.cursor()
        cur.execute(f"SELECT * FROM lotes WHERE id={PH} AND ativo=1", (lid,))
        r = cur.fetchone()
        if not r: raise HTTPException(404, "Lote não encontrado")
        r = row(r); qty = float(r["quantidade"])
        if m.tipo == "SAIDA":
            if m.quantidade > qty:
                raise HTTPException(400, f"Insuficiente — disponível: {qty}")
            qty -= m.quantidade
        elif m.tipo == "ENTRADA":
            qty += m.quantidade
        elif m.tipo == "AJUSTE":
            qty = m.quantidade
        cur.execute(f"UPDATE lotes SET quantidade={PH} WHERE id={PH}", (qty, lid))
        cur.execute(f"INSERT INTO movimentos (lote_id,tipo,quantidade,usuario,obs,pedido_ref) VALUES ({PH},{PH},{PH},{PH},{PH},{PH})",
                    (lid, m.tipo, m.quantidade, m.usuario, m.obs, m.pedido_ref))
        cur.execute(f"SELECT * FROM lotes WHERE id={PH}", (lid,))
        return enriquecer_lote(row(cur.fetchone()))

@router.post("/lotes/baixa-vencidos")
def baixa_vencidos(payload: dict):
    usuario = payload.get("usuario", "operador")
    obs = payload.get("obs", "Baixa de vencidos em lote")
    ids = payload.get("ids", [])
    ok = 0
    with db() as con:
        cur = con.cursor()
        if ids:
            phs = ",".join([PH]*len(ids))
            cur.execute(f"SELECT * FROM lotes WHERE id IN ({phs}) AND ativo=1 AND quantidade>0", ids)
        else:
            cur.execute(f"SELECT * FROM lotes WHERE validade<{PH} AND ativo=1 AND quantidade>0", (str(__import__('datetime').date.today()),))
        for r in cur.fetchall():
            d = row(r)
            con.cursor().execute(f"INSERT INTO movimentos (lote_id,tipo,quantidade,usuario,obs) VALUES ({PH},'SAIDA',{PH},{PH},{PH})",
                (d["id"], d["quantidade"], usuario, obs))
            con.cursor().execute(f"UPDATE lotes SET quantidade=0, ativo=0 WHERE id={PH}", (d["id"],))
            ok += 1
    return {"baixados": ok, "msg": f"{ok} lote(s) baixado(s)"}

@router.get("/fefo")
def fefo(sku: str, quantidade: float = 0):
    with db() as con:
        cur = con.cursor()
        cur.execute(f"SELECT * FROM lotes WHERE sku {like()} {PH} AND ativo=1 AND quantidade>0 ORDER BY validade ASC",
                    (sku.upper(),))
        dados = [enriquecer_lote(row(r)) for r in cur.fetchall()]
    if not dados: raise HTTPException(404, "Nenhum lote disponível")
    restante = quantidade
    for d in dados:
        d["separar"] = min(float(d["quantidade"]), restante) if restante > 0 else 0
        restante = max(0, restante - float(d["quantidade"]))
    return {"sku": sku.upper(), "descricao": dados[0].get("descricao",""),
            "total": sum(float(d["quantidade"]) for d in dados),
            "solicitado": quantidade, "ok": restante <= 0,
            "falta": restante, "lotes": dados}

@router.get("/skus")
def skus():
    with db() as con:
        cur = con.cursor()
        cur.execute("""SELECT sku, descricao, unidade,
            SUM(quantidade) as total, MIN(validade) as proxima_val,
            COUNT(*) as num_lotes
            FROM lotes WHERE ativo=1 GROUP BY sku,descricao,unidade ORDER BY sku""")
        return [dict(r) for r in cur.fetchall()]

@router.get("/skus/{sku_code}")
def sku_info(sku_code: str):
    with db() as con:
        cur = con.cursor()
        cur.execute(f"SELECT sku,descricao,unidade,fornecedor FROM lotes WHERE sku {like()} {PH} AND ativo=1 ORDER BY id DESC LIMIT 1",
                    (sku_code.upper(),))
        r = cur.fetchone()
    if not r: raise HTTPException(404, "SKU não encontrado")
    return dict(r)

@router.get("/movimentos")
def movimentos(lote_id: Optional[int] = None, limit: int = 100):
    with db() as con:
        cur = con.cursor()
        if lote_id:
            cur.execute(f"""SELECT m.*,l.sku,l.lote as num_lote,l.endereco
                FROM movimentos m LEFT JOIN lotes l ON m.lote_id=l.id
                WHERE m.lote_id={PH} ORDER BY m.data_mov DESC LIMIT {PH}""", (lote_id, limit))
        else:
            cur.execute(f"""SELECT m.*,l.sku,l.lote as num_lote,l.endereco
                FROM movimentos m LEFT JOIN lotes l ON m.lote_id=l.id
                ORDER BY m.data_mov DESC LIMIT {PH}""", (limit,))
        return [dict(r) for r in cur.fetchall()]

@router.post("/importar")
async def importar(payload: dict):
    linhas = payload.get("linhas", []); ok = 0; erros = []
    with db() as con:
        cur = con.cursor()
        from database import DATABASE_URL
        for i, l in enumerate(linhas, 1):
            try:
                sku  = l.get("sku","").strip().upper()
                lote = l.get("lote","").strip().upper()
                val  = normalizar_data(l.get("validade",""))
                end  = l.get("endereco","").strip().upper() or "SEM-END"
                qty  = float(str(l.get("quantidade","0")).replace(",",".") or 0)
                if not all([sku,lote,val]) or qty<=0:
                    erros.append(f"Linha {i}: incompleto"); continue
                if DATABASE_URL:
                    cur.execute(f"""INSERT INTO lotes (sku,descricao,lote,validade,quantidade,unidade,endereco,fornecedor)
                        VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH}) RETURNING id""",
                        (sku,l.get("descricao",""),lote,val,qty,l.get("unidade","UN") or "UN",end,l.get("fornecedor","")))
                    lid = cur.fetchone()["id"]
                else:
                    cur.execute(f"""INSERT INTO lotes (sku,descricao,lote,validade,quantidade,unidade,endereco,fornecedor)
                        VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH})""",
                        (sku,l.get("descricao",""),lote,val,qty,l.get("unidade","UN") or "UN",end,l.get("fornecedor","")))
                    lid = cur.lastrowid
                cur.execute(f"INSERT INTO movimentos (lote_id,tipo,quantidade,usuario) VALUES ({PH},'ENTRADA',{PH},'importacao')", (lid,qty))
                ok+=1
            except Exception as e: erros.append(f"Linha {i}: {e}")
    return {"importados": ok, "erros": erros}

@router.get("/exportar")
def exportar():
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM lotes WHERE ativo=1 ORDER BY validade")
        dados = cur.fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["sku","descricao","lote","validade","quantidade","unidade","endereco","fornecedor","data_receb"])
    for r in dados:
        d = dict(r)
        w.writerow([d.get(k,"") for k in ["sku","descricao","lote","validade","quantidade","unidade","endereco","fornecedor","data_receb"]])
    out.seek(0)
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition":"attachment; filename=estoque.csv"})

@router.get("/metricas")
def metricas():
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) as n FROM lotes WHERE ativo=1"); total = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(DISTINCT endereco) as n FROM lotes WHERE ativo=1"); ends = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(DISTINCT sku) as n FROM lotes WHERE ativo=1"); skus = cur.fetchone()["n"]
        cur.execute("SELECT validade FROM lotes WHERE ativo=1 AND quantidade>0")
        vals = [dict(r)["validade"] for r in cur.fetchall()]
    vencidos = sum(1 for v in vals if dias_vencer(v) < 0)
    atencao  = sum(1 for v in vals if 0 <= dias_vencer(v) <= 30)
    return {"total_lotes":total,"total_enderecos":ends,"total_skus":skus,
            "vencidos":vencidos,"atencao":atencao}
