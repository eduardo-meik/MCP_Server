# Conectarse al MCP de evaluación de prospectos

Necesitas tu cuenta corporativa (Microsoft o Google) y que tu administrador te haya dado acceso.
No hay API keys: inicias sesión con tu usuario de siempre.

URL del servidor (te la entrega tu administrador):

```
https://mcp-<empresa>-<número>.southamerica-west1.run.app/mcp
```

## Claude

1. Entra a **Customize → Connectors**, haz clic en **+** y elige **Add custom connector**.
2. Ponle un nombre (por ejemplo *Evaluación de prospectos*) y pega la URL.
3. Haz clic en **Add** y luego en **Connect**.
4. Inicia sesión con tu cuenta corporativa y acepta los permisos.

En una conversación, actívalo desde **+ → Connectors**.

**Claude Team o Enterprise:** un owner lo agrega una vez en
**Organization settings → Connectors → Add → Custom → Web**. Después, cada persona entra a
**Customize → Connectors** y hace clic en **Connect**.

## ChatGPT

1. Activa el modo desarrollador en **Settings → Security and login → Developer mode**
   (planes de pago).
2. Crea un conector o app personalizada con la misma URL.
3. Inicia sesión con tu cuenta corporativa.

## Claude Code

```bash
claude mcp add --transport http prospectos https://mcp-<empresa>-<número>.southamerica-west1.run.app/mcp
```

La primera vez que lo uses se abre el navegador para iniciar sesión.

## Probar la conexión

Pregunta: *"Valida el RUT 16.163.631-2"*. La validación de formato no tiene costo.
Para un informe completo: *"Evalúa al prospecto RUT …, nombre …, teléfono …"*.

## Buenas prácticas

- Cada consulta tiene costo y queda registrada con tu usuario.
- Úsalo solo para evaluar prospectos o prevenir fraude, no para otros fines.
- La recomendación es orientativa: la decisión final la toma una persona.
- Si una fuente aparece como "no concluyente", repite la consulta más tarde.
