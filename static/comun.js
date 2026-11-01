// Utilidades compartidas: navegación, configuración guardada y cálculo.
const CFG_KEY = "demanda_ti_ngr_cfg_v1";

function leerCfg() {
  try { return JSON.parse(localStorage.getItem(CFG_KEY) || "null"); } catch (e) { return null; }
}
function guardarCfg(cfg) {
  try { localStorage.setItem(CFG_KEY, JSON.stringify(cfg)); return true; } catch (e) { return false; }
}
function borrarCfg() { try { localStorage.removeItem(CFG_KEY); } catch (e) {} }

async function calcular(cfg) {
  const r = await fetch("/api/calcular", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(cfg || leerCfg() || {}),
  });
  if (!r.ok) throw new Error("Error " + r.status);
  return r.json();
}

function nav(activa) {
  const tabs = [["/", "Resumen"], ["/oficina", "Oficina"], ["/modelos", "Modelos"], ["/parametros", "Parámetros"]];
  const personal = leerCfg() ? '<span class="chip-cfg" title="Estás viendo resultados con parámetros modificados">Parámetros modificados</span>' : '<span class="chip-cfg base">Valores compartidos</span>';
  const html = `<nav class="tabs"><div class="brand">Demanda TI · Data NGR 2027</div>
    <div class="tablist">${tabs.map(([h, t]) => `<a href="${h}" class="${t === activa ? "on" : ""}">${t}</a>`).join("")}</div>${personal}</nav>`;
  document.body.insertAdjacentHTML("afterbegin", html);
}

const fmtH = n => Math.round(n).toLocaleString("es-PE");
const fmtPct = n => Math.round(n * 100) + "%";
