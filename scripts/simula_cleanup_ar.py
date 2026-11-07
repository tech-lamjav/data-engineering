"""Simulação LOCAL de uma cleanup policy do Artifact Registry sobre um listing real.

Por que existe: o `--dry-run` do próprio Artifact Registry só reporta no audit log de Data
Write, que está DESLIGADO no projeto `smartbetting-dados`. Aplicar a policy em dry-run não
mostraria nada. Este script lê o mesmo JSON de policy que vai ser aplicado (`infra/ar-cleanup-*.json`,
para a simulação e a policy não divergirem) e o listing do repositório, e diz o que seria
apagado, por pacote.

Uso (ver infra/README.md para o passo a passo):

    python3 scripts/simula_cleanup_ar.py <policy.json> <listing.json> <vivos.txt> \
        [--esperado N] [--indices indices.txt]

- `listing.json`: `gcloud artifacts docker images list <repo> --include-tags --format=json`.
- `vivos.txt`: qualquer texto com os digests VIVOS no formato
  `<host>-docker.pkg.dev/<projeto>/<repo>/<pacote>@sha256:<64 hex>` (ex.: a saída inteira do
  `deploy_cloud_run.sh --lista-vivos` ou do `scripts/vivos_jobs_dbt.sh`). OBRIGATÓRIO: sem vivo
  nenhum DESTE repositório a simulação sai com 2, não com verde (uma lista vazia, cortada ou
  de outro repo daria "nenhum vivo seria apagado" sem ter conferido nada).
- `--esperado N`: quantos digests vivos distintos deste repositório a lista tem de ter.
  Diferente de N = exit 2 (lista parcial: um serviço que falhou na leitura some calado).
- `--indices FILE`: linhas `indice <pai@sha256> <filho@sha256>` de `scripts/indices_ar.py`.
  Obrigatório se o listing tiver índice (manifest list/OCI index): sem ele a simulação não
  sabe quem é filho de quem e sai com 2.

Saídas: 0 = nenhum vivo cai; 1 = algum vivo cai (ou não está no listing, ou depende de um pai
que seria apagado); 2 = entrada insuficiente para responder.

Semântica reproduzida (docs.cloud.google.com/artifact-registry/docs/repositories/cleanup-policy):
- versão é apagada se casa com ALGUMA policy Delete e com NENHUMA Keep (Keep vence);
- `mostRecentVersions.keepCount` vale por pacote, ordenado pela criação da versão;
- `olderThan`/`newerThan` contam desde a criação da versão no repositório;
- `tagPrefixes` só casa versão com tag (`tagState: tagged`).
Campo de policy que este script não conhece é ERRO, não é ignorado: ignorar em silêncio
faria a simulação divergir da policy real sem ninguém ver.

ÍNDICES: o AR não apaga imagem referenciada por um índice que fica ("Cleanup policies for
Docker images don't delete images referenced by a parent manifest"); com `--indices` a
simulação aplica essa regra. O caminho inverso a doc deixa ambíguo ("If the parent manifest
is deleted, then any related images are deleted when the cleanup policy is next run"): um
filho mantido pela própria tag cujo pai seria apagado pode cair junto. Para um digest VIVO
isso é tratado como risco (exit 1, "tagueie o pai"), nunca como seguro.
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


def ler_indices(texto):
    """Linhas `indice <pai> <filho>` → {(pacote, versao_filho): {(pacote, versao_pai), ...}}."""
    pais = {}
    for linha in texto.splitlines():
        partes = linha.split()
        if not partes or partes[0] != "indice":
            continue
        if len(partes) != 3 or not all(DIGEST_VIVO.fullmatch(x) for x in partes[1:]):
            raise ValueError(f"linha de índice malformada: {linha!r}")
        pai, filho = (tuple(x.split("@", 1)) for x in partes[1:])
        pais.setdefault(filho, set()).add(pai)
    return pais


def _eh_indice(v):
    return "index" in v["media_type"] or "manifest.list" in v["media_type"]


def simular(policies, versoes, agora, pais=None):
    """Devolve {(pacote, versao): (decisao, motivo)} com decisao em {"apaga", "mantem"}.

    `pais` ({filho: {pai, ...}}, de `ler_indices`) aplica a regra do AR: filho referenciado
    por um índice que fica não é apagado."""
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

    # Ponto fixo: índice aninhado (índice cujo filho é índice) propaga em mais de um passo.
    mudou = True
    while mudou and pais:
        mudou = False
        for filho, seus_pais in pais.items():
            if resultado.get(filho, ("",))[0] != "apaga":
                continue
            mantido = [p for p in seus_pais if resultado.get(p, ("",))[0] == "mantem"]
            if mantido:
                resultado[filho] = ("mantem", "filho de índice mantido " + mantido[0][1][:19])
                mudou = True
    return resultado


def checar_vivos(vivos, versoes, resultado, pais=None):
    """Para cada digest vivo do(s) repositório(s) listado(s): está no listing? seria apagado?
    depende de um pai que seria apagado? Devolve lista de (digest, problema) — vazia = seguro."""
    pais = pais or {}
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
        elif pais.get((pacote, versao)) and all(
            resultado.get(p, ("apaga",))[0] == "apaga" for p in pais[(pacote, versao)]
        ):
            nomes = ", ".join(sorted(p[1][:19] for p in pais[(pacote, versao)]))
            problemas.append((vivo, f"é filho de índice que seria APAGADO ({nomes}); a cascata "
                                    f"é ambígua na doc do AR: tagueie o pai (ex.: live-prev-idx)"))
    return problemas


def vivos_do_repo(vivos, versoes):
    repos = {v["repo"] for v in versoes}
    return sorted({d for d in vivos if "/".join(d.split("@", 1)[0].split("/")[:3]) in repos})


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
            if _eh_indice(v):
                linhas.append(f"  · {d.split('/')[-1]} é um ÍNDICE: os filhos dele ficam "
                              f"enquanto ele ficar (--indices)")
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
    else:
        linhas.append("✓ Nenhum digest vivo seria apagado.")
    return "\n".join(linhas)


def _erro(msg):
    print(f"ERRO: {msg}", file=sys.stderr)
    return 2


def main(argv):
    args = list(argv[1:])
    esperado = None
    indices_path = None
    try:
        while "--esperado" in args:
            i = args.index("--esperado")
            esperado = int(args[i + 1])
            del args[i:i + 2]
        while "--indices" in args:
            i = args.index("--indices")
            indices_path = args[i + 1]
            del args[i:i + 2]
    except (IndexError, ValueError):
        return _erro("--esperado exige um inteiro e --indices exige um arquivo")
    if len(args) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    with open(args[0], encoding="utf-8") as f:
        policies = json.load(f)
    with open(args[1], encoding="utf-8") as f:
        versoes = normalizar_versoes(json.load(f))
    with open(args[2], encoding="utf-8") as f:
        vivos = DIGEST_VIVO.findall(f.read())

    aqui = vivos_do_repo(vivos, versoes)
    if not aqui:
        return _erro(f"nenhum digest vivo DESTE repositório em {args[2]}: sem isso a "
                     f"simulação não confere nada (lista vazia, cortada ou de outro repo?)")
    if esperado is not None and len(aqui) != esperado:
        return _erro(f"{len(aqui)} digest(s) vivo(s) deste repositório em {args[2]}, "
                     f"esperado {esperado}: lista parcial? (um serviço que falhou na leitura "
                     f"some da lista sem aviso)")

    pais = {}
    if indices_path:
        try:
            with open(indices_path, encoding="utf-8") as f:
                pais = ler_indices(f.read())
        except ValueError as e:
            return _erro(str(e))
    elif any(_eh_indice(v) for v in versoes):
        return _erro("o listing tem índices (manifest list/OCI index) e faltou --indices: "
                     "sem saber quem é filho de quem a simulação não responde. Rode "
                     "scripts/indices_ar.py sobre o mesmo listing.")

    try:
        resultado = simular(policies, versoes, datetime.now(timezone.utc), pais)
    except PolicyInvalida as e:
        return _erro(f"policy inválida: {e}")
    problemas = checar_vivos(vivos, versoes, resultado, pais)
    print(relatorio(versoes, resultado, problemas, vivos))
    return 1 if problemas else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
