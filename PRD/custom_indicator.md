## Resultado da Pesquisa — Indicadores Personalizados no TopstepX

Investiguei fundo a plataforma e encontrei informações muito valiosas. Aqui está o quadro completo:

---

### O que descobri

**A plataforma usa o TradingView Advanced Charts (charting_library) versão `TT v31.1.0`** — não o TradingView.com público. Isso significa que:

- **Não existe Pine Script Editor** — essa funcionalidade é exclusiva do TradingView.com e foi deliberadamente removida nessa versão licenciada
- O modal de "Indicators" lista apenas indicadores built-in + 2 indicadores customizados da própria Topstep: `Color Link` e `Topstep Daily Levels`
- O menu de contexto (botão direito) não tem opção de criar scripts

---

### A brecha que encontrei — `getCustomIndicators`

O widget tem uma propriedade chamada **`getCustomIndicators`** no objeto de configuração global `window['tradingview_3f9fc']`. Essa função retorna uma Promise com os indicadores customizados que aparecem na lista.

**Confirmei que ela pode ser interceptada via JavaScript** — e o patch funciona durante a sessão atual.

O formato de um indicador customizado é em **JavaScript puro** (não Pine Script), com esta estrutura:

```javascript
{
  name: "Meu Indicador",
  metainfo: {
    _metainfoVersion: 51,
    id: "meu_indicador@tv-basicstudies-1",
    name: "Meu Indicador",
    description: "Descrição",
    shortDescription: "Short",
    isCustomIndicator: true,
    is_price_study: true,
    plots: [{ id: "plot_0", type: "line" }],
    styles: { plot_0: { title: "Linha", color: "blue" } },
    defaults: { styles: { plot_0: { color: "blue", linewidth: 2 } }, inputs: {} },
    inputs: []
  },
  constructor: function() {
    this.init = function(ctx, inputs) { this._context = ctx; };
    this.main = function(ctx, inputs) {
      // lógica aqui - retorna array com os valores dos plots
      // ctx.new_var(), ctx._source[], etc.
      return [/* valor do plot_0 */];
    };
  }
}
```

---

### Métodos práticos para adicionar indicadores

**Método 1 — Extensão do Chrome (permanente e mais confiável)**

É a abordagem mais robusta. Você cria uma extensão com um `content_script` que injeta o código **antes** da página carregar, interceptando a criação do widget:

```javascript
// content_script.js - injeta no contexto da página
const script = document.createElement('script');
script.textContent = `
  // Aguarda o objeto de config ser criado e intercepta
  const originalDefineProperty = Object.defineProperty;
  // Ou monitora window com Proxy para pegar tradingview_3f9fc
  const handler = { set(obj, prop, val) {
    if (prop === 'tradingview_3f9fc' && val.getCustomIndicators) {
      const orig = val.getCustomIndicators.bind(val);
      val.getCustomIndicators = () => orig().then(list => [...list, MEU_INDICADOR]);
    }
    return Reflect.set(obj, prop, val);
  }};
  // window = new Proxy(window, handler); // não funciona diretamente
  // Alternativa: polling + patch
`;
document.head.prepend(script);
```

**Método 2 — Userscript com Tampermonkey/Violentmonkey (mais fácil)**

Instala o Tampermonkey no Chrome e cria um script com `@run-at document-start`:

```javascript
// ==UserScript==
// @name         TopstepX Custom Indicators
// @match        https://topstepx.com/trade*
// @run-at       document-start
// ==/UserScript==

// Intercepta antes do widget carregar
const _origSet = Object.getOwnPropertyDescriptor(window, 'tradingview_3f9fc');

// Polling rápido para pegar o objeto de config
const interval = setInterval(() => {
  const w = window['tradingview_3f9fc'];
  if (w && w.getCustomIndicators) {
    clearInterval(interval);
    const orig = w.getCustomIndicators.bind(w);
    w.getCustomIndicators = () => orig().then(list => [...list, {
      name: "Meu EMA Custom",
      metainfo: { /* ... */ },
      constructor: function() {
        this.init = function(ctx) { this._context = ctx; };
        this.main = function(ctx) { 
          // EMA simples
          return [/* cálculo */]; 
        };
      }
    }]);
  }
}, 50);
```

**Método 3 — Console do navegador (temporário, para testes)**

Diretamente no console do Chrome (`F12`), depois que a página carrega — **mas o indicador só vai aparecer após reabrir o modal de Indicators**:

```javascript
const w = window['tradingview_3f9fc'];
const orig = w.getCustomIndicators.bind(w);
w.getCustomIndicators = () => orig().then(list => [...list, { /* seu indicador */ }]);
```

---

### Limitações importantes

- Os indicadores customizados usam a **API JavaScript da charting_library**, não Pine Script. Você tem acesso a dados de candles, mas não às funções `ta.*` do Pine Script
- A lógica precisa ser escrita em JavaScript — porém é possível converter a maioria dos indicadores Pine Script para JS
- Sem extensão/userscript, o indicador some ao dar F5
- O Topstep pode atualizar a plataforma e mudar o nome do objeto de config (`tradingview_3f9fc` é gerado dinamicamente), então o script precisa ser robusto para isso

Quer que eu te ajude a escrever um indicador específico nesse formato, ou a estruturar o userscript/extensão completo?