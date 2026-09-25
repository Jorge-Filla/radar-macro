# -*- coding: utf-8 -*-
"""Radar macro: busca semanal de itens novos (Fronteira do conhecimento ou Analises de ponta).

Uso: python coletor.py fronteira
     python coletor.py analises

Gera semana/<grupo>.md apenas com itens vistos pela primeira vez nos
ultimos 7 dias. Rodar de novo na mesma semana nao perde itens.
"""
import html
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

RAIZ = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (radar-macro; leitura pessoal semanal)"}
JANELA_DIAS = 7            # itens novos que entram no arquivo da semana
IDADE_MAX_DIAS = 21        # ignora itens publicados ha mais tempo que isso
GUARDAR_VISTOS_DIAS = 400  # memoria de itens ja vistos
MAX_POR_FONTE = 40
TAM_RESUMO = 320

PALAVRAS_MACRO = [
    r"inflat", r"monetar", r"interest rate", r"central bank", r"macro",
    r"business cycle", r"recession", r"output gap", r"\bgdp\b", r"fiscal",
    r"public debt", r"sovereign", r"exchange rate", r"currenc", r"capital flow",
    r"current account", r"term structure", r"yield", r"\bbonds?\b", r"credit",
    r"\bbank", r"financial stab", r"financial cris", r"liquidity",
    r"unemployment", r"labor market", r"labour market", r"phillips", r"wage",
    r"\bprices?\b", r"pricing", r"expectation", r"productivity",
    r"economic growth", r"\bshocks?\b", r"\bvar\b", r"autoregress", r"\bdsge\b",
    r"nowcast", r"forecast", r"time series", r"volatil", r"\btrade\b", r"tariff",
    r"commodit", r"\boil\b", r"housing", r"asset pric", r"heterogeneous agent",
    r"\bhank\b", r"natural rate", r"r-star", r"stablecoin", r"\bcbdc\b",
    r"quantitative easing", r"balance sheet", r"monetary union", r"stagflation",
    r"cointegrat", r"local projection", r"structural break", r"risk premi",
]
RE_MACRO = re.compile("|".join(PALAVRAS_MACRO), re.IGNORECASE)
PALAVRAS_MACRO_ESTRITO = [
    r"inflat", r"monetar", r"interest rate", r"central bank", r"macro",
    r"business cycle", r"recession", r"\bvar\b", r"vector autoregress",
    r"local projection", r"nowcast", r"forecast", r"time series", r"\bdsge\b",
    r"term structure", r"yield curve", r"volatil", r"cointegrat",
    r"structural break", r"factor model", r"phillips", r"\bgdp\b",
    r"exchange rate", r"asset pric", r"fiscal", r"impulse response",
]
RE_MACRO_ESTRITO = re.compile("|".join(PALAVRAS_MACRO_ESTRITO), re.IGNORECASE)
RE_LIXO = re.compile(r"^(front ?matter|back ?matter|corrigendum|erratum|editorial|"
                     r"issue information|masthead|table of contents|announcement)",
                     re.IGNORECASE)


def limpa(texto):
    if not texto:
        return ""
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = html.unescape(texto)
    texto = re.sub("[\u200b\u200c\u200d\ufeff]", "", texto)
    return re.sub(r"\s+", " ", texto).strip()


def corta(texto, n=TAM_RESUMO):
    texto = limpa(texto)
    if len(texto) <= n:
        return texto
    return texto[:n].rsplit(" ", 1)[0] + "..."


def baixa(url, tentativas=3):
    ultimo = None
    for i in range(tentativas):
        try:
            r = requests.get(url, headers=UA, timeout=40)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001
            ultimo = e
            time.sleep(3 * (i + 1))
    raise ultimo


def data_iso(struct):
    if not struct:
        return ""
    return time.strftime("%Y-%m-%d", struct)


# ---------- leitores por tipo de fonte ----------

def le_rss(fonte):
    feed = feedparser.parse(baixa(fonte["url"]).content)
    if not feed.entries:
        raise RuntimeError("feed sem itens")
    itens = []
    for e in feed.entries:
        link = e.get("link", "").split("#")[0]
        if fonte.get("link_contem") and fonte["link_contem"] not in link:
            continue
        titulo = limpa(e.get("title", ""))
        autores = ", ".join(a.get("name", "") for a in e.get("authors", []) if a.get("name"))
        if " -- by " in titulo:  # padrao do NBER
            titulo, autores = titulo.split(" -- by ", 1)
        titulo = re.sub(r"^(FEDS Paper|FEDS Note|IFDP Paper):\s*", "", titulo)
        titulo = re.sub(r"^BC - ", "", titulo)
        data = data_iso(e.get("published_parsed") or e.get("updated_parsed"))
        itens.append({
            "id": e.get("id") or link,
            "titulo": titulo.strip(),
            "autores": limpa(autores),
            "link": link,
            "data": data,
            "resumo": e.get("summary", ""),
        })
    return itens


def le_arxiv(fonte):
    feed = feedparser.parse(baixa(fonte["url"]).content)
    if not feed.entries:
        raise RuntimeError("arXiv sem itens")
    itens = []
    for e in feed.entries:
        ident = re.sub(r"v\d+$", "", e.get("id", ""))
        itens.append({
            "id": ident,
            "titulo": limpa(e.get("title", "")),
            "autores": ", ".join(a.get("name", "") for a in e.get("authors", [])),
            "link": ident.replace("http://", "https://"),
            "data": data_iso(e.get("published_parsed")),
            "resumo": e.get("summary", ""),
        })
    return itens


def le_crossref(fonte):
    desde = (datetime.now(timezone.utc) - timedelta(days=IDADE_MAX_DIAS)).strftime("%Y-%m-%d")
    url = (f"https://api.crossref.org/journals/{fonte['issn']}/works"
           f"?filter=from-created-date:{desde},type:journal-article"
           f"&sort=created&order=desc&rows=60"
           f"&select=DOI,title,author,abstract,created,URL")
    dados = baixa(url).json()["message"]["items"]
    itens = []
    for x in dados:
        titulo = limpa((x.get("title") or [""])[0])
        autores = ", ".join(
            f"{a.get('given', '')} {a.get('family', '')}".strip() for a in x.get("author", [])
        )
        itens.append({
            "id": "doi:" + x["DOI"].lower(),
            "titulo": titulo,
            "autores": autores,
            "link": "https://doi.org/" + x["DOI"],
            "data": x.get("created", {}).get("date-time", "")[:10],
            "resumo": re.sub(r"^\s*Abstract\s*", "", limpa(x.get("abstract", ""))),
        })
    return itens


def le_bcb_wp(fonte):
    dados = baixa(fonte["url"]).json()
    itens = []
    for r in dados.get("Rows", []):
        link = (r.get("Path") or "").replace("edicao-www.bcb.gov.br", "www.bcb.gov.br")
        num = (r.get("NumeroOWSNMBR") or "").split(".")[0]
        itens.append({
            "id": "bcbwp:" + (num or link),
            "titulo": limpa(r.get("Title", "")) + (f" (TD {num})" if num else ""),
            "autores": limpa(r.get("AutoresOWSTEXT", "")),
            "link": link,
            "data": (r.get("DataReferenciaOWSDATE") or "")[:10],
            "resumo": r.get("AbstractOWSMTXT") or r.get("ResumoOWSMTXT") or "",
        })
    if not itens:
        raise RuntimeError("BCB sem itens")
    return itens


LEITORES = {"rss": le_rss, "arxiv": le_arxiv, "crossref": le_crossref, "bcb_wp": le_bcb_wp}


# ---------- estado ----------

def carrega(caminho, padrao):
    if caminho.exists():
        return json.loads(caminho.read_text(encoding="utf-8"))
    return padrao


def salva(caminho, dados):
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")


def main(grupo):
    fontes = json.loads((RAIZ / "fontes.json").read_text(encoding="utf-8"))[grupo]
    hoje = datetime.now(timezone.utc).date()
    limite_idade = (hoje - timedelta(days=IDADE_MAX_DIAS)).isoformat()
    inicio_janela = (hoje - timedelta(days=JANELA_DIAS)).isoformat()

    arq_estado = RAIZ / "estado" / f"{grupo}.json"
    estado = carrega(arq_estado, None)
    if estado is None:  # primeira execucao: so a ultima semana
        estado = {"vistos": {}, "recentes": []}
        limite_idade = inicio_janela
    vistos = estado["vistos"]

    falhas, contagem = [], {}
    for fonte in fontes:
        nome = fonte["nome"]
        try:
            itens = LEITORES[fonte["tipo"]](fonte)
        except Exception as e:  # noqa: BLE001
            falhas.append(f"{nome}: {str(e)[:120]}")
            continue
        novos = 0
        for it in itens[:100]:
            if not it["titulo"] or RE_LIXO.search(it["titulo"]):
                continue
            if it["id"] in vistos:
                continue
            vistos[it["id"]] = hoje.isoformat()
            if it["data"] and it["data"] < limite_idade:
                continue  # antigo demais: marca como visto e ignora
            texto = it["titulo"] + " " + limpa(it["resumo"])
            filtro = fonte.get("filtro_macro")
            if filtro == "estrito" and not RE_MACRO_ESTRITO.search(texto):
                continue
            if filtro is True and not RE_MACRO.search(texto):
                continue
            if novos >= MAX_POR_FONTE:
                continue
            it["resumo"] = corta(it["resumo"])
            it["fonte"] = nome
            it["visto_em"] = hoje.isoformat()
            estado["recentes"].append(it)
            novos += 1
        contagem[nome] = novos

    # limpa memoria antiga
    corte_vistos = (hoje - timedelta(days=GUARDAR_VISTOS_DIAS)).isoformat()
    estado["vistos"] = {k: v for k, v in vistos.items() if v >= corte_vistos}
    estado["recentes"] = [i for i in estado["recentes"] if i["visto_em"] > inicio_janela]
    salva(arq_estado, estado)

    # arquivo da semana
    titulo_grupo = {"fronteira": "Fronteira do conhecimento", "analises": "Analises de ponta"}[grupo]
    linhas = [f"# Radar macro | {titulo_grupo} | atualizado em {hoje.isoformat()}",
              f"Itens novos dos ultimos {JANELA_DIAS} dias: {len(estado['recentes'])}"]
    if falhas:
        linhas.append("Fontes com falha nesta busca: " + "; ".join(falhas))
    for fonte in fontes:
        grupo_itens = [i for i in estado["recentes"] if i["fonte"] == fonte["nome"]]
        if not grupo_itens:
            continue
        linhas.append(f"\n## {fonte['nome']} ({len(grupo_itens)})")
        for i in grupo_itens:
            linhas.append(f"\n- {i['titulo']}")
            if i["autores"]:
                linhas.append(f"  Autores: {corta(i['autores'], 160)}")
            linhas.append(f"  Link: {i['link']}")
            if i["data"]:
                linhas.append(f"  Data: {i['data']}")
            if i["resumo"]:
                linhas.append(f"  Resumo: {i['resumo']}")
    saida = RAIZ / "semana" / f"{grupo}.md"
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text("\n".join(linhas) + "\n", encoding="utf-8")

    print(f"[{grupo}] novos por fonte: {json.dumps(contagem, ensure_ascii=False)}")
    if falhas:
        print(f"[{grupo}] falhas: {falhas}")
    if len(falhas) == len(fontes):
        sys.exit("todas as fontes falharam")


if __name__ == "__main__":
    main(sys.argv[1])
