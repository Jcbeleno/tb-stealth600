--!nonstrict
-- Glifo + % de batería del Stealth 600. Micrófono silenciado -> glifo microphone-off (como el
-- distintivo de mute de Swarm en la bandeja); cargando -> battery-charging. Se oculta si tb600d no
-- ve el transmisor.

local STATE = (noctalia.getenv("HOME") or "") .. "/.cache/tb600/state.json"
local STALE_MS = 30000

local function render()
  local body = noctalia.fileExists(STATE) and noctalia.readFile(STATE)
  local st = body and noctalia.json.decode(body)
  local fresh = type(st) == "table" and st.time and (noctalia.nowMs() - st.time * 1000) < STALE_MS
  if not fresh or not st.connected then
    barWidget.setVisible(false)
    return
  end
  barWidget.setVisible(true)
  local s = st.settings or {}
  local pct = st.battery
  local lines = { "Stealth 600 Gen 3" }
  if st.headset_link == false then
    barWidget.setGlyph("headphones")
    barWidget.setText("off")
    table.insert(lines, "Headset apagado o fuera de alcance")
  else
    barWidget.setGlyph(s.mic_mute and "microphone-off" or (st.charging and "battery-charging" or "headphones"))
    barWidget.setText(pct and (tostring(pct) .. "%") or "?")
    table.insert(lines, "Batería: " .. (pct and (pct .. "%") or "desconocida") .. (st.charging and " (cargando)" or ""))
    if s.mic_mute then table.insert(lines, "Micrófono silenciado") end
    if s.shh then table.insert(lines, "Superhuman Hearing activo") end
  end
  barWidget.setTooltip(table.concat(lines, "\n"))
end

function update()
  render()
end

function onClick()
  noctalia.runAsync("tb600-gui >/dev/null 2>&1 &", function() end)
end

noctalia.setUpdateInterval(2000)
render()
