from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import httpx, json
from database import db, PH, like, rows, row, datetime_default
from helpers import normalizar_data

router = APIRouter(prefix="/api/pcp", tags=["pcp"])

# ── Qlik ───────────────────────────────────────────────────────
async def qlik_fetch(tenant_url: str, api_key: str, app_id: str, campos: list) -> list:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    url = f"https://{tenant_url}/api/v1/apps/{app_id}/qix-export"
    body = {"columns": campos}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code != 200:
            raise Exception(f"Qlik API erro {r.status_code}: {r.text[:200]}")
        return r.json().get("data", [])

def get_qlik_config():
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT chave,valor FROM config WHERE chave IN ('qlik_tenant','qlik_key','qlik_app')")
        return {r["chave"]: r["valor"] for r in cur.fetchall()}

# ── modelos ────────────────────────────────────────────────────
class PedidoIn(BaseModel):
    num_pedido:    str
    data_pedido:   Optional[str] = ""
    cliente:       Optional[str] = ""
    sku:           str
    descricao:     Optional[str] = ""
    quantidade:    float
    transportadora: Optional[str] = ""
    servico:       Optional[str] = ""
    campanha:      Optional[str] = ""
    fornecedor:    Optional[str] = ""
    data_entrega:  Optional[str] = ""
    prioridade:    Optional[int] = 2

class OndaIn(BaseModel):
    nome:           str
    transportadora: Optional[str] = None
    servico:        Optional[str] = None
    campanha:       Optional[str] = None
    fornecedor:     Optional[str] = None
    data_entrega:   Optional[str] = None
    prioridade:     Optional[int] = None
    pedidos_ids:    Optional[List[int]] = None

class QlikConfig(BaseModel):
    tenant:  str
    api_key: str
    app_id:  str

# ── pedidos ────────────────────────────────────────────────────
@router.get("/pedidos")
def listar(status:str="", transportadora:str="", servico:str="",
           campanha:str="", prioridade:str="", onda_id:str="", sku:str=""):
    with db() as con:
        cur = con.cursor()
        q = "SELECT * FROM pedidos WHERE 1=1"; p = []
        if status:        q+=f" AND status={PH}"; p.append(status)
        if transportadora: q+=f" AND transportadora {like()} {PH}"; p.append(f"%{transportadora}%")
        if servico:       q+=f" AND servico {like()} {PH}"; p.append(f"%{servico}%")
        if campanha:      q+=f" AND campanha {like()} {PH}"; p.append(f"%{campanha}%")
        if prioridade:    q+=f" AND prioridade<={PH}"; p.append(int(prioridade))
        if onda_id:       q+=f" AND onda_id={PH}"; p.append(int(onda_id))
        if sku:           q+=f" AND sku {like()} {PH}"; p.append(f"%{sku}%")
        q += " ORDER BY prioridade ASC, data_entrega ASC, num_pedido ASC"
        cur.execute(q, p)
        return [dict(r) for r in cur.fetchall()]

@router.post("/pedidos", status_code=201)
def criar(p: PedidoIn):
    with db() as con:
        cur = con.cursor()
        from database import DATABASE_URL
        val  = normalizar_data(p.data_entrega) if p.data_entrega else ""
        dpd  = normalizar_data(p.data_pedido)  if p.data_pedido  else ""
        args = (p.num_pedido.upper(), dpd, p.cliente, p.sku.upper(), p.descricao,
                p.quantidade, p.transportadora, p.servico, p.campanha,
                p.fornecedor, val, p.prioridade, "manual")
        if DATABASE_URL:
            cur.execute(f"""INSERT INTO pedidos (num_pedido,data_pedido,cliente,sku,descricao,quantidade,
                transportadora,servico,campanha,fornecedor,data_entrega,prioridade,origem)
                VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH}) RETURNING id""", args)
            pid = cur.fetchone()["id"]
        else:
            cur.execute(f"""INSERT INTO pedidos (num_pedido,data_pedido,cliente,sku,descricao,quantidade,
                transportadora,servico,campanha,fornecedor,data_entrega,prioridade,origem)
                VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH})""", args)
            pid = cur.lastrowid
        cur.execute(f"SELECT * FROM pedidos WHERE id={PH}", (pid,))
        return dict(cur.fetchone())

@router.delete("/pedidos/{pid}")
def remover(pid: int):
    with db() as con:
        con.cursor().execute(f"DELETE FROM pedidos WHERE id={PH}", (pid,))
    return {"ok": True}

@router.post("/pedidos/importar")
async def importar(payload: dict):
    linhas = payload.get("linhas", []); ok = 0; erros = []
    # mapeamento automático de colunas PRW/Qlik → campos internos
    mapa = {
        "pedidoformatado":"num_pedido","pedido_formatado":"num_pedido","pedido":"num_pedido",
        "data_pedido":"data_pedido","datapedido":"data_pedido",
        "cliente":"cliente","destinatario":"cliente","shipdestination":"cliente",
        "proprovidercode":"sku","codigo":"sku",
        "proname":"descricao","nome_produto":"descricao",
        "logname":"transportadora",
        "nome":"servico","shippingtype":"servico",
        "idcampaign":"campanha","nomecampanha":"campanha",
        "nomearmazem":"fornecedor",
        "data_entrega":"data_entrega","dataentrega":"data_entrega",
    }
    with db() as con:
        cur = con.cursor()
        from database import DATABASE_URL
        for i, raw in enumerate(linhas, 1):
            try:
                l = {mapa.get(k.lower().replace(" ","_"), k.lower()): v for k, v in raw.items()}
                num  = str(l.get("num_pedido","")).strip().upper()
                sku  = str(l.get("sku","")).strip().upper()
                qty  = float(str(l.get("quantidade","0")).replace(",",".") or 0)
                if not num or not sku or qty<=0:
                    erros.append(f"Linha {i}: incompleto"); continue
                val = ""; dpd = ""
                try: val = normalizar_data(l["data_entrega"]) if l.get("data_entrega") else ""
                except: pass
                try: dpd = normalizar_data(l["data_pedido"]) if l.get("data_pedido") else ""
                except: pass
                pri = int(l.get("prioridade", 2) or 2)
                args = (num, dpd, l.get("cliente",""), sku, l.get("descricao",""), qty,
                        l.get("transportadora",""), l.get("servico",""), l.get("campanha",""),
                        l.get("fornecedor",""), val, pri, l.get("origem","importacao"))
                if DATABASE_URL:
                    cur.execute(f"""INSERT INTO pedidos (num_pedido,data_pedido,cliente,sku,descricao,quantidade,
                        transportadora,servico,campanha,fornecedor,data_entrega,prioridade,origem)
                        VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH}) RETURNING id""", args)
                    cur.fetchone()
                else:
                    cur.execute(f"""INSERT INTO pedidos (num_pedido,data_pedido,cliente,sku,descricao,quantidade,
                        transportadora,servico,campanha,fornecedor,data_entrega,prioridade,origem)
                        VALUES ({PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH},{PH})""", args)
                ok += 1
            except Exception as e: erros.append(f"Linha {i}: {e}")
    return {"importados": ok, "erros": erros}

# ── integração Qlik ────────────────────────────────────────────
@router.post("/qlik/config")
def salvar_config(c: QlikConfig):
    with db() as con:
        cur = con.cursor()
        for chave, valor in [("qlik_tenant",c.tenant),("qlik_key",c.api_key),("qlik_app",c.app_id)]:
            cur.execute(f"DELETE FROM config WHERE chave={PH}", (chave,))
            cur.execute(f"INSERT INTO config (chave,valor) VALUES ({PH},{PH})", (chave, valor))
    return {"ok": True}

@router.get("/qlik/status")
def qlik_status():
    cfg = get_qlik_config()
    return {"configurado": bool(cfg.get("qlik_tenant") and cfg.get("qlik_key") and cfg.get("qlik_app")),
            "tenant": cfg.get("qlik_tenant",""), "app_id": cfg.get("qlik_app","")}

@router.post("/qlik/sincronizar")
async def sincronizar_qlik():
    cfg = get_qlik_config()
    if not all([cfg.get("qlik_tenant"), cfg.get("qlik_key"), cfg.get("qlik_app")]):
        raise HTTPException(400, "Qlik não configurado — acesse Configurações > Qlik")
    try:
        campos = ["PedidoFormatado","Data_Pedido","DESTINATARIO","sku","nome_produto",
                  "Quantidade","logName","Nome","idCampaign","nomeArmazem","statusDate"]
        dados_brutos = await qlik_fetch(cfg["qlik_tenant"], cfg["qlik_key"], cfg["qlik_app"], campos)
        if not dados_brutos:
            return {"importados": 0, "msg": "Nenhum dado retornado pelo Qlik"}
        linhas = []
        for r in dados_brutos:
            linhas.append({
                "num_pedido":    r.get("PedidoFormatado",""),
                "data_pedido":   r.get("Data_Pedido",""),
                "cliente":       r.get("DESTINATARIO",""),
                "sku":           r.get("sku",""),
                "descricao":     r.get("nome_produto",""),
                "quantidade":    r.get("Quantidade","0"),
                "transportadora":r.get("logName",""),
                "servico":       r.get("Nome",""),
                "campanha":      str(r.get("idCampaign","")),
                "fornecedor":    r.get("nomeArmazem",""),
                "data_entrega":  r.get("statusDate",""),
                "origem":        "qlik",
            })
        result = await importar({"linhas": linhas})
        return {"importados": result["importados"], "erros": result["erros"],
                "msg": f"{result['importados']} pedido(s) sincronizado(s) do Qlik"}
    except Exception as e:
        raise HTTPException(500, f"Erro ao conectar ao Qlik: {str(e)}")

# ── ondas ──────────────────────────────────────────────────────
@router.get("/ondas")
def listar_ondas():
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT * FROM ondas ORDER BY id DESC")
        return [dict(r) for r in cur.fetchall()]

@router.post("/ondas", status_code=201)
def criar_onda(o: OndaIn):
    with db() as con:
        cur = con.cursor()
        q = "SELECT * FROM pedidos WHERE status='pendente'"; p = []; criterios = []
        if o.transportadora: q+=f" AND transportadora {like()} {PH}"; p.append(f"%{o.transportadora}%"); criterios.append(f"Transp: {o.transportadora}")
        if o.servico:        q+=f" AND servico {like()} {PH}";        p.append(f"%{o.servico}%");        criterios.append(f"Serviço: {o.servico}")
        if o.campanha:       q+=f" AND campanha {like()} {PH}";       p.append(f"%{o.campanha}%");       criterios.append(f"Campanha: {o.campanha}")
        if o.fornecedor:     q+=f" AND fornecedor {like()} {PH}";     p.append(f"%{o.fornecedor}%");     criterios.append(f"Fornec: {o.fornecedor}")
        if o.data_entrega:   q+=f" AND data_entrega<={PH}";           p.append(o.data_entrega);          criterios.append(f"Entrega≤{o.data_entrega}")
        if o.prioridade:     q+=f" AND prioridade<={PH}";             p.append(o.prioridade);            criterios.append(f"Prio≤{o.prioridade}")
        if o.pedidos_ids:
            phs = ",".join([PH]*len(o.pedidos_ids))
            q += f" AND id IN ({phs})"; p += o.pedidos_ids
        cur.execute(q, p); pedidos = cur.fetchall()
        if not pedidos: raise HTTPException(400, "Nenhum pedido pendente com esses critérios")
        total_peds = len(set(dict(p)["num_pedido"] for p in pedidos))
        total_skus = len(set(dict(p)["sku"] for p in pedidos))
        total_itens = sum(float(dict(p)["quantidade"]) for p in pedidos)
        from database import DATABASE_URL
        if DATABASE_URL:
            cur.execute(f"""INSERT INTO ondas (nome,criterios,total_pedidos,total_skus,total_itens)
                VALUES ({PH},{PH},{PH},{PH},{PH}) RETURNING id""",
                (o.nome, " · ".join(criterios) or "Todos pendentes", total_peds, total_skus, total_itens))
            oid = cur.fetchone()["id"]
        else:
            cur.execute(f"""INSERT INTO ondas (nome,criterios,total_pedidos,total_skus,total_itens)
                VALUES ({PH},{PH},{PH},{PH},{PH})""",
                (o.nome, " · ".join(criterios) or "Todos pendentes", total_peds, total_skus, total_itens))
            oid = cur.lastrowid
        ids = [dict(p)["id"] for p in pedidos]
        phs = ",".join([PH]*len(ids))
        cur.execute(f"UPDATE pedidos SET status='em_onda', onda_id={PH} WHERE id IN ({phs})", [oid]+ids)
        cur.execute(f"SELECT * FROM ondas WHERE id={PH}", (oid,))
        return dict(cur.fetchone())

@router.get("/ondas/{oid}/pedidos")
def pedidos_onda(oid: int):
    with db() as con:
        cur = con.cursor()
        cur.execute(f"SELECT * FROM pedidos WHERE onda_id={PH} ORDER BY prioridade,data_entrega,num_pedido", (oid,))
        return [dict(r) for r in cur.fetchall()]

@router.delete("/ondas/{oid}")
def cancelar_onda(oid: int):
    with db() as con:
        cur = con.cursor()
        cur.execute(f"UPDATE pedidos SET status='pendente', onda_id=NULL WHERE onda_id={PH}", (oid,))
        cur.execute(f"DELETE FROM ondas WHERE id={PH}", (oid,))
    return {"ok": True}

@router.get("/metricas")
def metricas():
    with db() as con:
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) as n FROM pedidos WHERE status='pendente'"); pend=cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM pedidos WHERE status='em_onda'");  onda=cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM ondas WHERE status='aberta'");     ondas=cur.fetchone()["n"]
        cur.execute("SELECT COUNT(DISTINCT num_pedido) as n FROM pedidos");       total=cur.fetchone()["n"]
        cur.execute(f"SELECT COUNT(*) as n FROM pedidos WHERE data_entrega<{PH} AND data_entrega!='' AND status!='expedido'",
                    (str(__import__('datetime').date.today()),)); atrasados=cur.fetchone()["n"]
    return {"pendentes":pend,"em_onda":onda,"ondas_abertas":ondas,
            "total_pedidos":total,"atrasados":atrasados}

@router.get("/filtros")
def filtros_disponiveis():
    with db() as con:
        cur = con.cursor()
        def vals(col):
            cur.execute(f"SELECT DISTINCT {col} FROM pedidos WHERE {col}!='' AND status='pendente' ORDER BY {col}")
            return [dict(r)[col] for r in cur.fetchall()]
        return {"transportadoras":vals("transportadora"),"servicos":vals("servico"),
                "campanhas":vals("campanha"),"fornecedores":vals("fornecedor")}
