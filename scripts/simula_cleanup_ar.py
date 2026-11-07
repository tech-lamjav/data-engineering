"""Simulação LOCAL de uma cleanup policy do Artifact Registry sobre um listing real.

Por que existe: o `--dry-run` do próprio Artifact Registry só reporta no audit log de Data
Write, que está DESLIGADO no projeto `smartbetting-dados`. Aplicar a policy em dry-run não
mostraria nada. Este script lê o mesmo JSON de policy que vai ser aplicado (`infra/ar-cleanup-*.json`,
para a simulação e a policy não divergirem) e o listing do repositório, e diz o que seria
apagado, por pacote.

Uso (ver infra/README.md para o passo a passo):

    gcloud artifacts docker images list \\
        us-east1-docker.pkg.dev/smartbetting-dados/cloud-run-source-deploy \\
        --include-tags --format=json > /tmp/imagens.json
    ./scripts/deploy_cloud_run.sh --lista-vivos > /tmp/vivos.txt
    python3 scripts/simula_cleanup_ar.py infra/ar-cleanup-cloud-run-source-deploy.json \\
        /tmp/imagens.json /tmp/vivos.txt

O terceiro argumento (opcional) é qualquer texto com os digests VIVOS no formato
`<host>-docker.pkg.dev/<projeto>/<repo>/<pacote>@sha256:<64 hex>`; o script extrai todos os
que achar. Sai com 1 se algum vivo do repositório listado seria apagado (ou não está no
listing): é o sinal de que a tag `live` ainda não foi aplicada (backfill) e a policy NÃO pode
ser aplicada.

Semântica reproduzida (docs.cloud.google.com/artifact-registry/docs/repositories/cleanup-policy):
- versão é apagada se casa com ALGUMA policy Delete e com NENHUMA Keep (Keep vence);
- `mostRecentVersions.keepCount` vale por pacote, ordenado pela criação da versão;
- `olderThan`/`newerThan` contam desde a criação da versão no repositório;
- `tagPrefixes` só casa versão com tag (`tagState: tagged`).
Campo de policy que este script não conhece é ERRO, não é ignorado: ignorar em silêncio
faria a simulação divergir da policy real sem ninguém ver.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone

DIGEST_VIVO = re.compile(r"[a-z0-9-]+-docker\.pkg\.dev/[^\s@]+@sha256:[0-9a-f]{64}")
DURACAO = re.compile(r"^(\d+)([smhd])$")
UNIDADES = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
CAMPOS_CONDICAO = {"tagState", "tagPrefixes", "versionNamePrefixes", "packageNamePrefixes",
                   "olderThan", "newerThan"}
CAMPOS_RECENTES = {"keepCount", "packageNamePrefixes"}
CAMPOS_POLICY = {"name", "action", "condition", "mostRecentVersions"}


class PolicyInvalida(ValueError):
    pass


def duracao(texto):
    m = DURACAO.match(str(texto))
    if not m:
        raise PolicyInvalida(f"duração fora do formato <n>[smhd]: {texto!r}")
    return timedelta(**{UNIDADES[m.group(2)]: int(m.group(1))})


def validar_policies(policies):
    if not isinstance(policies, list) or not policies:
        raise PolicyInvalida("o arquivo de policy tem de ser uma lista JSON não vazia")
    for p in policies:
        nome = p.get("name", "<sem nome>")
        extras = set(p) - CAMPOS_POLICY
        if extras:
            raise PolicyInvalida(f"{nome}: campos desconhecidos {sorted(extras)}")
        tipo = (p.get("action") or {}).get("type")
        if tipo not in ("Keep", "Delete"):
            raise PolicyInvalida(f"{nome}: action.type tem de ser Keep ou Delete, veio {tipo!r}")
        if "condition" in p and "mostRecentVersions" in p:
            raise PolicyInvalida(f"{nome}: condition e mostRecentVersions na mesma policy")
        if "mostRecentVersions" in p:
            if tipo != "Keep":
                raise PolicyInvalida(f"{nome}: mostRecentVersions só existe em Keep")
            extras = set(p["mostRecentVersions"]) - CAMPOS_RECENTES
            if extras:
                raise PolicyInvalida(f"{nome}: mostRecentVersions com campos {sorted(extras)}")
            if not isinstance(p["mostRecentVersions"].get("keepCount"), int):
                raise PolicyInvalida(f"{nome}: keepCount inteiro obrigatório")
            continue
        cond = p.get("condition")
        if not cond:
            raise PolicyInvalida(f"{nome}: policy sem condition nem mostRecentVersions")
        extras = set(cond) - CAMPOS_CONDICAO
        if extras:
            raise PolicyInvalida(f"{nome}: condition com campos desconhecidos {sorted(extras)}")
        estado = cond.get("tagState", "any").lower()
        if estado not in ("tagged", "untagged", "any"):
            raise PolicyInvalida(f"{nome}: tagState inválido {cond['tagState']!r}")
        if cond.get("tagPrefixes") and estado != "tagged":
            raise PolicyInvalida(f"{nome}: tagPrefixes exige tagState tagged")
        for campo in ("olderThan", "newerThan"):
            if campo in cond:
                duracao(cond[campo])


def _data(texto):
    # gcloud devolve RFC 3339 com fração de tamanho variável ("...:41.123456Z").
    texto = texto.replace("Z", "+00:00")
    m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", texto)
    if m and m.group(2):
        texto = m.group(1) + m.group(2)[:7].ljust(7, "0") + m.group(3)
    return datetime.fromisoformat(texto)


def _bytes(valor):
    # Índice multi-arch/attestation vem com imageSizeBytes "None" (string) no listing real.
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


def normalizar_versoes(listing):
    """Listing de `gcloud artifacts docker images list --include-tags --format=json` →
    dicts com pacote, nome do pacote (sem host/projeto/repo), versão, tags, criação."""
    versoes = []
    for item in listing:
        tags = item.get("tags") or []
        if isinstance(tags, str):  # versões antigas do gcloud juntam com vírgula
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        pacote = item["package"]
        versoes.append({
            "pacote": pacote,
            "nome_pacote": "/".join(pacote.split("/")[3:]),
            "repo": "/".join(pacote.split("/")[:3]),
            "versao": item["version"],
            "tags": tags,
            "criacao": _data(item["createTime"]),
            "media_type": (item.get("metadata") or {}).get("mediaType", ""),
            "bytes": _bytes((item.get("metadata") or {}).get("imageSizeBytes")),
        })
    return versoes


def _casa_condicao(v, cond, agora):
    estado = cond.get("tagState", "any").lower()
    if estado == "tagged" and not v["tags"]:
        return False
    if estado == "untagged" and v["tags"]:
        return False
    if cond.get("tagPrefixes") and not any(
        t.startswith(p) for t in v["tags"] for p in cond["tagPrefixes"]
    ):
        return False
    if cond.get("versionNamePrefixes") and not any(
        v["versao"].startswith(p) for p in cond["versionNamePrefixes"]
    ):
        return False
    if cond.get("packageNamePrefixes") and not any(
        v["nome_pacote"].startswith(p) for p in cond["packageNamePrefixes"]
    ):
        return False
    idade = agora - v["criacao"]
    if "olderThan" in cond and not idade > duracao(cond["olderThan"]):
        return False
    if "newerThan" in cond and not idade < duracao(cond["newerThan"]):
        return False
    return True


def simular(policies, versoes, agora):
    """Devolve {(pacote, versao): (decisao, motivo)} com decisao em {"apaga", "mantem"}."""
    validar_policies(policies)
    recentes = {}  # policy name -> set((pacote, versao)) mantidas por mostRecentVersions
    for p in policies:
        if "mostRecentVersions" not in p:
            continue
        regra = p["mostRecentVersions"]
        prefixos = regra.get("packageNamePrefixes")
        por_pacote = {}
        for v in versoes:
            if prefixos and not any(v["nome_pacote"].startswith(x) for x in prefixos):
                continue
            por_pacote.setdefault(v["pacote"], []).append(v)
        mantidas = set()
        for lista in por_pacote.values():
            lista.sort(key=lambda v: v["criacao"], reverse=True)
            mantidas.update((v["pacote"], v["versao"]) for v in lista[: regra["keepCount"]])
        recentes[p["name"]] = mantidas

    resultado = {}
    for v in versoes:
        chave = (v["pacote"], v["versao"])
        keeps, deletes = [], []
        for p in policies:
            if "mostRecentVersions" in p:
                casa = chave in recentes[p["name"]]
            else:
                casa = _casa_condicao(v, p["condition"], agora)
            if casa:
                (keeps if p["action"]["type"] == "Keep" else deletes).append(p["name"])
        if deletes and not keeps:
            resultado[chave] = ("apaga", ",".join(deletes))
        elif keeps:
            resultado[chave] = ("mantem", ",".join(keeps))
        else:
            resultado[chave] = ("mantem", "nenhuma policy casa")
    return resultado


def checar_vivos(vivos, versoes, resultado):
    """Para cada digest vivo do(s) repositório(s) listado(s): está no listing? seria apagado?
    Devolve lista de (digest, problema) — vazia = seguro."""
    repos = {v["repo"] for v in versoes}
    indice = {(v["pacote"], v["versao"]): v for v in versoes}
    problemas = []
    for vivo in sorted(set(vivos)):
        pacote, versao = vivo.split("@", 1)
        if "/".join(pacote.split("/")[:3]) not in repos:
            continue  # vivo de outro repositório; não é deste listing
        if (pacote, versao) not in indice:
            problemas.append((vivo, "NÃO está no listing (já apagado? listing incompleto?)"))
        elif resultado[(pacote, versao)][0] == "apaga":
            problemas.append((vivo, "seria APAGADO (falta a tag live?)"))
    return problemas


def relatorio(versoes, resultado, problemas, vivos):
    linhas = []
    por_pacote = {}
    for v in versoes:
        p = por_pacote.setdefault(v["nome_pacote"], {"total": 0, "apaga": 0, "bytes": 0})
        p["total"] += 1
        if resultado[(v["pacote"], v["versao"])][0] == "apaga":
            p["apaga"] += 1
            p["bytes"] += v["bytes"]
    linhas.append(f"{'pacote':40} {'versoes':>8} {'apaga':>6} {'mantem':>7} {'GiB livres':>11}")
    for nome in sorted(por_pacote):
        p = por_pacote[nome]
        linhas.append(f"{nome:40} {p['total']:>8} {p['apaga']:>6} {p['total'] - p['apaga']:>7} "
                      f"{p['bytes'] / 2**30:>11.2f}")
    total = sum(p["total"] for p in por_pacote.values())
    apaga = sum(p["apaga"] for p in por_pacote.values())
    gib = sum(p["bytes"] for p in por_pacote.values()) / 2**30
    linhas.append(f"{'TOTAL':40} {total:>8} {apaga:>6} {total - apaga:>7} {gib:>11.2f}")
    linhas.append("(GiB livres é teto: camadas compartilhadas entre versões não somem duas vezes)")

    indice = {f"{v['pacote']}@{v['versao']}": v for v in versoes}
    vivos_aqui = [d for d in sorted(set(vivos)) if d in indice]
    if vivos:
        linhas.append("")
        linhas.append(f"Digests vivos neste listing: {len(vivos_aqui)}")
        for d in vivos_aqui:
            v = indice[d]
            # Índice multi-arch/attestation tem filhos SEM tag; a policy não apaga filho
            # referenciado por um pai vivo, mas vale saber que eles existem.
            if "index" in v["media_type"] or "manifest.list" in v["media_type"]:
                linhas.append(f"  ⚠ {d.split('/')[-1]} é um ÍNDICE ({v['media_type']}): "
                              f"filhos sem tag no listing")
    vivos_tag = {d for d, v in indice.items() if any(t.startswith("live") for t in v["tags"])}
    sobras = sorted(vivos_tag - set(vivos_aqui)) if vivos else []
    if sobras:
        linhas.append(f"Versões com tag live* que não servem tráfego (Keep permanente): {len(sobras)}")
        for d in sobras:
            linhas.append(f"  - {d.split('/')[-1]} {indice[d]['tags']}")

    linhas.append("")
    if problemas:
        linhas.append(f"✗ INSEGURO: {len(problemas)} digest(s) vivo(s) em risco. NÃO aplique a policy.")
        for d, msg in problemas:
            linhas.append(f"  - {d}: {msg}")
    elif vivos:
        linhas.append("✓ Nenhum digest vivo seria apagado.")
    else:
        linhas.append("⚠ Sem lista de vivos: a simulação não conferiu o que está em produção.")
    return "\n".join(linhas)


def main(argv):
    if len(argv) not in (3, 4):
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        policies = json.load(f)
    with open(argv[2], encoding="utf-8") as f:
        versoes = normalizar_versoes(json.load(f))
    vivos = []
    if len(argv) == 4:
        with open(argv[3], encoding="utf-8") as f:
            vivos = DIGEST_VIVO.findall(f.read())
        if not vivos:
            print(f"ERRO: nenhum digest vivo reconhecido em {argv[3]}", file=sys.stderr)
            return 2
    try:
        resultado = simular(policies, versoes, datetime.now(timezone.utc))
    except PolicyInvalida as e:
        print(f"ERRO: policy inválida: {e}", file=sys.stderr)
        return 2
    problemas = checar_vivos(vivos, versoes, resultado)
    print(relatorio(versoes, resultado, problemas, vivos))
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
