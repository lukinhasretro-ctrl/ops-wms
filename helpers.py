from datetime import datetime, date

def normalizar_data(val: str) -> str:
    val = str(val).strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d",
                "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except:
            pass
    raise ValueError(f"Data inválida: {val}")

def dias_vencer(validade: str) -> int:
    try:
        v = datetime.strptime(str(validade)[:10], "%Y-%m-%d").date()
        return (v - date.today()).days
    except:
        return 999

def status_validade(dias: int) -> str:
    if dias < 0:    return "vencido"
    if dias <= 7:   return "critico"
    if dias <= 30:  return "atencao"
    return "ok"

def enriquecer_lote(d: dict) -> dict:
    dias = dias_vencer(d.get("validade", ""))
    d["dias_validade"] = dias
    d["status_val"]    = status_validade(dias)
    return d

def prio_label(p: int) -> str:
    return {1: "Alta", 2: "Média", 3: "Baixa"}.get(p, "—")
