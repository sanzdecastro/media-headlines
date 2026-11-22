"""
Elige con Claude el titular principal de cada medio entre sus candidatos y lo
cataloga: temática (lista fija) e historia (qué medios cuentan la misma noticia).
Solo agrupa por tema; no interpreta ni compara cómo lo cuenta cada medio.

Una sola llamada con todos los medios, porque para agrupar historias hay que
verlos juntos. La IA devuelve el ÍNDICE del candidato, no texto: el titular
siempre es uno que estaba en la portada.

clasificar() nunca lanza: si no hay ANTHROPIC_API_KEY o algo falla, devuelve {}
y titulares.py se queda con la heurística (el primer candidato).
"""
import json
import os

import anthropic

MODEL = "claude-sonnet-5-5"
SONNET_PRECIO = (2.00, 10.00)  # $ por millón de tokens de entrada / salida, para el log

TEMATICAS = [
    "Política nacional",
    "Internacional",
    "Economía",
    "Sociedad",
    "Tribunales y sucesos",
    "Deportes",
    "Cultura y ocio",
    "Ciencia y tecnología",
    "Clima y medio ambiente",
    "Opinión",
    "Otros",
]

SYSTEM = f"""Analizas las portadas de periódicos digitales españoles para un comparador de titulares.

Para cada medio recibes una lista numerada de candidatos: los enlaces más destacados de su portada, \
ordenados de mayor a menor tamaño de letra, con el tamaño en píxeles y la distancia al borde superior \
como pistas de jerarquía visual. En los medios marcados como RSS no hay esas pistas: el orden es el del \
feed de portada.

Para cada medio:
1. `candidato`: el índice de la noticia que el medio destaca como principal en su portada. No elijas \
promociones, suscripciones, servicios (el tiempo, la lotería), tests o encuestas para el lector, \
secciones, avisos de cookies ni enlaces a especiales genéricos («Toda la información de…»). Ante la \
duda, pesa más la letra más grande y la posición más alta.
2. `tematica`: una de la lista cerrada.
3. `historia_id`: los medios cuyo titular principal trata el mismo hecho o la misma noticia comparten \
historia. Si el titular no coincide con el de ningún otro medio, tiene historia propia.

No interpretes ni compares cómo cuenta la noticia cada medio: solo clasifica de qué tema habla.

En `historias`, cada historia usada lleva un `id` corto en minúsculas con guiones y una `etiqueta` \
breve y neutral en castellano que describa el hecho (no el titular de un medio concreto).

Temáticas posibles: {", ".join(TEMATICAS)}."""


def _esquema(nombres):
    return {
        "type": "object",
        "properties": {
            "medios": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "media": {"type": "string", "enum": nombres},
                        "candidato": {"type": "integer"},
                        "tematica": {"type": "string", "enum": TEMATICAS},
                        "historia_id": {"type": "string"},
                    },
                    "required": ["media", "candidato", "tematica", "historia_id"],
                    "additionalProperties": False,
                },
            },
            "historias": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "etiqueta": {"type": "string"},
                    },
                    "required": ["id", "etiqueta"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["medios", "historias"],
        "additionalProperties": False,
    }


def _prompt(candidatos):
    bloques = []
    for nombre, lista in candidatos.items():
        rss = "fs" not in lista[0]
        lineas = [f"## {nombre}" + (" (RSS)" if rss else "")]
        for i, c in enumerate(lista):
            pista = "" if rss else f"[{c['fs']}px, y={c['y']}] "
            lineas.append(f"{i}. {pista}{c['text']}")
        bloques.append("\n".join(lineas))
    return "\n\n".join(bloques)


def _validar(datos, candidatos):
    historias = {h["id"]: h["etiqueta"] for h in datos["historias"]}
    resultado = {}
    for m in datos["medios"]:
        nombre, idx = m["media"], m["candidato"]
        if nombre in resultado or not 0 <= idx < len(candidatos[nombre]):
            print(f"  ! clasificador: respuesta no válida para {nombre}, se usa la heurística")
            continue
        hid = m["historia_id"]
        resultado[nombre] = {
            "candidato": idx,
            "tematica": m["tematica"],
            "historia": {"id": hid, "etiqueta": historias.get(hid, "")},
        }
    return resultado


def clasificar(candidatos):
    """{ nombre: {candidato, tematica, historia} } para los medios que pudo clasificar."""
    if not candidatos:
        return {}
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("  · sin ANTHROPIC_API_KEY: se usa la heurística")
        return {}

    try:
        client = anthropic.Anthropic(timeout=180, max_retries=2)
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=8000,
            # Si el modelo se niega (falso positivo de seguridad), el servidor reintenta
            # con el modelo de respaldo que toque.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={
                "effort": "low",
                "format": {"type": "json_schema", "schema": _esquema(list(candidatos))},
            },
            system=SYSTEM,
            messages=[{"role": "user", "content": _prompt(candidatos)}],
        )
    except anthropic.APIStatusError as e:
        print(f"  ! clasificador: error {e.status_code} de la API ({e.__class__.__name__}), se usa la heurística")
        return {}
    except anthropic.APIConnectionError as e:
        print(f"  ! clasificador: sin conexión con la API ({e}), se usa la heurística")
        return {}

    u = response.usage
    coste = (u.input_tokens * SONNET_PRECIO[0] + u.output_tokens * SONNET_PRECIO[1]) / 1e6
    print(f"  · clasificador: {response.model}, {u.input_tokens} tokens de entrada, "
          f"{u.output_tokens} de salida (~{coste:.3f} $), stop_reason={response.stop_reason}")

    if response.stop_reason != "end_turn":
        print("  ! clasificador: respuesta incompleta o rechazada, se usa la heurística")
        return {}
    texto = next((b.text for b in reversed(response.content) if b.type == "text"), None)
    try:
        return _validar(json.loads(texto), candidatos)
    except (TypeError, ValueError, KeyError) as e:
        print(f"  ! clasificador: JSON no válido ({e}), se usa la heurística")
        return {}
