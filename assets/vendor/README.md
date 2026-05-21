# `assets/vendor/`

Bundles JS de terceiros vendored para reduzir dependência runtime de CDN externo.

## Status

Vazio no momento. M-2 do security review da fusão prevê hospedar aqui o bundle `@supabase/supabase-js@2.45.4` consumido pelo componente Web Notifications em [src/live.py](../../src/live.py).

## Procedimento manual (quando for fazer o vendor)

Requer download autorizado pelo operador humano (não pode ser feito pelo agente — supply chain):

```bash
# Da raiz do projeto:
curl -fsSL "https://esm.sh/@supabase/supabase-js@2.45.4?bundle&target=es2022" \
  -o assets/vendor/supabase-js-2.45.4.esm.js

# Verificar tamanho (esperado: ~300-500 KB) e cabecalho
wc -c assets/vendor/supabase-js-2.45.4.esm.js
head -1 assets/vendor/supabase-js-2.45.4.esm.js
```

Depois, em [src/live.py](../../src/live.py), trocar:

```python
const { createClient } = await import("https://esm.sh/@supabase/supabase-js@2.45.4");
```

por algo como:

```python
# Servir o bundle via Streamlit static
const VENDOR_URL = "/app/static/supabase-js-2.45.4.esm.js";
const { createClient } = await import(VENDOR_URL);
```

(Streamlit serve `assets/` em `/app/static/` por default em deploys próprios; em Streamlit Cloud, verificar o path correto.)

## Por que vendored

- Elimina dependência runtime de `esm.sh` (CDN externo na rota crítica de notificações).
- Garante integridade (commit pinned ao SHA do arquivo no git).
- Reduz superfície de ataque supply chain — bundle congelado até decisão consciente de bump.

## Quando atualizar

Quando o Supabase publicar uma versão com fix de segurança relevante. Bump explícito + recommit. Backlog: integrar `subresource integrity` no `<script>` para defesa adicional caso o arquivo seja servido de Storage público.
