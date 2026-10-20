-- PaperCards: Hammerspoon 端，负责全局快捷键、取选中内容/截图、识别来源、弹出快速录入面板
--   ⌃⌥⌘C  收集选中文字
--   ⌃⌥⌘S  用 Snipaste 截图收集图表
--   ⌃⌥⌘R  按 Snipaste 上次区域一键截图
--   ⌃⌥⌘A  开始/停止自动截图（区域内换页就截）
--   ⌃⌥⌘V  有选中文字收文字，否则收剪贴板内容（Snipaste 截图后）
--   ⌃⌥⌘P  打开卡片墙

local M = {}
-- 项目目录 = 本文件所在目录（可在 init.lua 里先设置 PAPERCARDS_DIR 覆盖）
local DIR = PAPERCARDS_DIR or debug.getinfo(1, "S").source:match("^@(.*)/[^/]*$") or (os.getenv("HOME") .. "/papercards")
local SERVER = "http://127.0.0.1:8787"
local MODS = { "ctrl", "alt", "cmd" }

local serverTask, panel
local panelUC          -- 当前面板的消息回调对象（保留引用，稳妥起见）
local captureBusyAt    -- 正在收集（还没弹出面板）的开始时间，用来忽略连按
local postCount = 0
local panelClosedHook   -- 面板关闭后调用一次（自动截图用它恢复监视）

local function firePanelClosed()
  local h = panelClosedHook
  panelClosedHook = nil
  if h then hs.timer.doAfter(0.3, h) end
end

-- ---------------------------------------------------------------- server

local function serverUp()
  local code = hs.http.get(SERVER .. "/api/ping")
  return code == 200
end

local function ensureServer()
  if serverUp() then return true end
  if not (serverTask and serverTask:isRunning()) then
    serverTask = hs.task.new("/usr/bin/python3", nil, { DIR .. "/server.py" })
    serverTask:start()
  end
  for _ = 1, 30 do
    hs.timer.usleep(100000)
    if serverUp() then return true end
  end
  hs.alert.show("PaperCards 服务启动失败，请在终端运行 python3 " .. DIR .. "/server.py 查看报错")
  return false
end

-- ---------------------------------------------------------------- context

local BROWSERS = {
  ["com.google.Chrome"] = "Google Chrome",
  ["com.microsoft.edgemac"] = "Microsoft Edge",
  ["com.brave.Browser"] = "Brave Browser",
  ["company.thebrowser.Browser"] = "Arc",
}

-- 读当前网页的选中文字和 citation_* 元数据（需要 Chrome 开启“允许 Apple 事件中的 JavaScript”）
local PAGE_JS = [[JSON.stringify({sel: String(window.getSelection()), meta: Array.from(document.querySelectorAll('meta[name],meta[property]')).reduce(function(o, m) { var k = (m.getAttribute('name') || m.getAttribute('property')).toLowerCase(); if (/^(citation_|dc\.|og:)/.test(k)) { (o[k] = o[k] || []).push(m.content); } return o; }, {})})]]

-- 取窗口的上下文（应用、标题、网页 URL 等）。不传 win 时用当前最前面的窗口
local function getContext(win)
  local front = win == nil
  win = win or hs.window.focusedWindow()
  local app = win and win:application() or hs.application.frontmostApplication()
  local ctx = {
    app = app and app:name() or "",
    window_title = win and win:title() or "",
    win = win,
  }
  local bid = app and app:bundleID()
  local browser = BROWSERS[bid]
  if browser then
    -- 找到这个窗口在浏览器里的序号：窗口标题以活动标签页标题开头
    local idx = 1
    if not front then
      local ok, tabs = hs.osascript.applescript(string.format(
        'tell application "%s"\nset out to {}\nrepeat with w in windows\nset end of out to title of active tab of w\nend repeat\nreturn out\nend tell', browser))
      if ok and type(tabs) == "table" then
        for i, t in ipairs(tabs) do
          if t ~= "" and ctx.window_title:find(t, 1, true) == 1 then idx = i break end
        end
      end
    end
    local ok, res = hs.osascript.applescript(string.format(
      'tell application "%s" to return {URL, title} of active tab of window %d', browser, idx))
    if ok and type(res) == "table" then ctx.url, ctx.page_title = res[1], res[2] end
    local js = PAGE_JS:gsub('\\', '\\\\'):gsub('"', '\\"')
    local ok2, out = hs.osascript.applescript(string.format(
      'tell application "%s" to execute active tab of window %d javascript "%s"', browser, idx, js))
    if ok2 and type(out) == "string" then
      local data = hs.json.decode(out)
      if data then ctx.selection, ctx.meta = data.sel, data.meta end
    end
  elseif bid == "com.apple.Safari" then
    local ok, res = hs.osascript.applescript('tell application "Safari" to return {URL, name} of current tab of front window')
    if ok and type(res) == "table" then ctx.url, ctx.page_title = res[1], res[2] end
  end
  return ctx
end

-- 找到覆盖某个区域中心点的最上层窗口（自动截图用它确定来源）
local function windowAtRegion(r)
  local cx, cy = r.x + r.w / 2, r.y + r.h / 2
  for _, w in ipairs(hs.window.orderedWindows()) do
    local f = w:frame()
    local app = w:application()
    if app and app:bundleID() ~= "org.hammerspoon.Hammerspoon"
        and cx >= f.x and cx <= f.x + f.w and cy >= f.y and cy <= f.y + f.h then
      return w
    end
  end
end

-- 记录剪贴板最近一次出现图片的时间：刚用 Snipaste 复制的截图，⌃⌥⌘V 优先收它
local lastImageAt, restoring = 0, false
M._pbWatcher = hs.pasteboard.watcher.new(function()
  if restoring then return end
  local types = hs.pasteboard.contentTypes() or {}
  for _, t in ipairs(types) do
    if t == "public.png" or t == "public.tiff" then lastImageAt = hs.timer.secondsSinceEpoch() return end
  end
end)
M._pbWatcher:start()

-- 模拟 ⌘C 取选中文字，取完把剪贴板恢复原样
local function copySelection()
  local saved = hs.pasteboard.readAllData()
  local count = hs.pasteboard.changeCount()
  -- 等用户松开 ⌃⌥，否则阅读器收到的是 ⌃⌥⌘C 而不是 ⌘C
  for _ = 1, 75 do
    local m = hs.eventtap.checkKeyboardModifiers()
    if not (m.ctrl or m.alt or m.shift) then break end
    hs.timer.usleep(20000)
  end
  hs.eventtap.keyStroke({ "cmd" }, "c", 0)
  for _ = 1, 50 do
    hs.timer.usleep(20000)
    if hs.pasteboard.changeCount() ~= count then break end
  end
  local text
  if hs.pasteboard.changeCount() ~= count then
    text = hs.pasteboard.getContents()
    hs.timer.doAfter(0.2, function()
      if saved then
        restoring = true
        hs.pasteboard.writeAllData(saved)
        hs.timer.doAfter(1, function() restoring = false end)
      end
    end)
  end
  return text
end

-- ---------------------------------------------------------------- panel

local function closePanel(refocus)
  if panel then
    local p = panel
    panel = nil
    p:delete()
    firePanelClosed()
  end
  if refocus and refocus:isVisible() then refocus:focus() end
end

local function showPanel(id, prevWin)
  closePanel()
  local wv
  local uc = hs.webview.usercontent.new("papercards")
  uc:setCallback(function(msg)
    if msg.body ~= "close" then return end
    if panel == wv then closePanel(prevWin)          -- 当前面板：正常关闭并回到阅读窗口
    elseif wv then wv:delete() end                  -- 已被新面板取代的旧面板：只关掉它自己
  end)
  panelUC = uc
  local screen = (prevWin and prevWin:screen()) or hs.mouse.getCurrentScreen()
  local f = screen:frame()
  local w, h = 480, 420
  local rect = { x = f.x + (f.w - w) / 2, y = f.y + f.h * 0.18, w = w, h = h }
  wv = hs.webview.new(rect, { developerExtrasEnabled = true }, uc)
    :windowTitle("PaperCards · 新卡片")
    :windowStyle({ "titled", "closable" })
    :allowTextEntry(true)
    :closeOnEscape(false)
    :deleteOnClose(true)
    :level(hs.drawing.windowLevels.floating)
    -- 「closing」回调在 delete() 之后才异步触发：只能清理自己，不能清掉已经换上的新面板
    :windowCallback(function(action)
      if action == "closing" and panel == wv then panel = nil; firePanelClosed() end
    end)
    :url(SERVER .. "/quick?id=" .. id)
    :show()
  panel = wv
  panel:bringToFront(true)
  hs.timer.doAfter(0.05, function()
    if panel == wv and wv:hswindow() then wv:hswindow():focus() end
  end)
end

local function post(payload, prevWin)
  postCount = postCount + 1
  hs.http.asyncPost(SERVER .. "/api/capture", hs.json.encode(payload), { ["Content-Type"] = "application/json" },
    function(code, body)
      captureBusyAt = nil
      if code ~= 200 then
        print("PaperCards capture failed: " .. tostring(code) .. " " .. tostring(body))
        hs.alert.show("PaperCards 保存失败：" .. tostring(code))
        return
      end
      showPanel(hs.json.decode(body).id, prevWin)
    end)
end

local function payloadFrom(ctx)
  return {
    app = ctx.app, window_title = ctx.window_title,
    url = ctx.url, page_title = ctx.page_title, meta = ctx.meta,
  }
end

-- ---------------------------------------------------------------- actions

function M.captureText()
  if not ensureServer() then return end
  local ctx = getContext()
  local text = ctx.selection
  if not text or text == "" then text = copySelection() end
  if not text or text:match("^%s*$") then
    print("PaperCards: 未取到选中文字, app=" .. tostring(ctx.app))
    hs.alert.show("没有选中文字（也可以先 ⌘C 复制，再按 ⌃⌥⌘V）")
    return
  end
  local p = payloadFrom(ctx)
  p.kind, p.text = "text", text
  post(p, ctx.win)
end

-- 截图交给 Snipaste：用它的命令行把截图直接存成文件，等文件出现后建卡片
local SNIPASTE = "/Applications/Snipaste.app/Contents/MacOS/Snipaste"
local snipTask, snipPoller, snipSeq = nil, nil, 0

-- 调 Snipaste 截图存成文件，文件写完后回调 cb(path)；超时（被取消）不回调
local function snipToFile(args, timeout, cb)
  if snipPoller then snipPoller:stop() end
  snipSeq = snipSeq + 1
  local path = string.format("%s/papercards-%d-%d.png", hs.fs.temporaryDirectory(), os.time(), snipSeq)
  os.remove(path)
  local cmd = { "snip" }
  for _, a in ipairs(args) do cmd[#cmd + 1] = a end
  cmd[#cmd + 1] = "-o"
  cmd[#cmd + 1] = path
  snipTask = hs.task.new(SNIPASTE, nil, cmd)
  snipTask:start()
  local started, lastSize = hs.timer.secondsSinceEpoch(), -1
  snipPoller = hs.timer.doEvery(0.2, function()
    local attr = hs.fs.attributes(path)
    if attr and attr.size > 0 and attr.size == lastSize then   -- 文件写完了
      snipPoller:stop(); snipPoller = nil
      cb(path)
    elseif hs.timer.secondsSinceEpoch() - started > timeout then
      snipPoller:stop(); snipPoller = nil
      if cb ~= nil and timeout < 30 then cb(nil) end
    end
    lastSize = attr and attr.size or -1
  end)
end

local function snip(args, timeout, ctx)
  snipToFile(args, timeout, function(path)
    if not path then captureBusyAt = nil return end
    local p = payloadFrom(ctx)
    p.kind, p.image_path = "image", path
    post(p, ctx.win)
  end)
end

-- Snipaste 框选截图，选好后（双击/回车/⌘C）直接建卡片
function M.captureScreenshot()
  if not ensureServer() then return end
  snip({}, 180, getContext())
end

-- 按 Snipaste 上一次成功截图的区域一键截图
function M.captureLastRegion()
  if not ensureServer() then return end
  snip({ "--last" }, 8, getContext())
end

-- 自动截图：开始时锁定 Snipaste 上次的区域（可在菜单里重新框选），每隔一会儿截一张，画面换页后弹出录入面板；面板开着时暂停监视
local watching, watchCount, watchMenu, watchCtx = false, 0, nil, nil
local watchRegion, watchBorder   -- 监视区域在开始时锁定，之后用 Snipaste 截别的不影响
local CONFIRM_DELAY = 0.8   -- 发现变化后隔这么久再截一帧确认，避开翻页动画

local function watchInterval()
  return hs.settings.get("papercards.watchInterval") or 5
end

-- 从 Snipaste 日志里读出上一次截图的区域（全局坐标，单位点）
local function snipasteLastRegion()
  local f = io.open(os.getenv("HOME") .. "/.snipaste/splog.txt", "rb")
  if not f then return nil end
  local size = f:seek("end")
  f:seek("set", math.max(0, size - 200000))
  local tail = f:read("*a")
  f:close()
  local x, y, w, h
  for a, b, c, d in tail:gmatch("Screenshot %((%-?%d+), (%-?%d+), (%d+) x (%d+)%)") do
    x, y, w, h = a, b, c, d
  end
  if x then return { x = tonumber(x), y = tonumber(y), w = tonumber(w), h = tonumber(h) } end
end

-- 监视用的截图：有屏幕录制权限就用系统 screencapture 静默截取 Snipaste 的上次区域（不闪屏），
-- 否则退回调用 Snipaste
local shotTask
local function watchShot(cb)
  local r = watchRegion
  if not r then return cb(nil) end
  if not hs.screenRecordingState() then   -- 没权限时退回 Snipaste（会闪屏）
    return snipToFile({ "--area", tostring(r.x), tostring(r.y), tostring(r.w), tostring(r.h) }, 8, cb)
  end
  snipSeq = snipSeq + 1
  local path = string.format("%s/papercards-%d-%d.png", hs.fs.temporaryDirectory(), os.time(), snipSeq)
  shotTask = hs.task.new("/usr/sbin/screencapture", function(code)
    cb(code == 0 and hs.fs.attributes(path) and path or nil)
  end, { "-x", string.format("-R%d,%d,%d,%d", r.x, r.y, r.w, r.h), path })
  shotTask:start()
end

-- 在监视区域外围画一圈细边框（画在区域外面，不会被截进去；不拦截鼠标）
local function showWatchBorder()
  if watchBorder then watchBorder:delete(); watchBorder = nil end
  local r = watchRegion
  if not r or not watching then return end
  local pad = 3
  watchBorder = hs.canvas.new({ x = r.x - pad, y = r.y - pad, w = r.w + pad * 2, h = r.h + pad * 2 })
  watchBorder[1] = {
    type = "rectangle", action = "stroke", strokeWidth = 2, strokeDashPattern = { 6, 4 },
    strokeColor = { red = 0.9, green = 0.45, blue = 0.25, alpha = 0.8 },
    frame = { x = 1, y = 1, w = r.w + pad * 2 - 2, h = r.h + pad * 2 - 2 },
  }
  watchBorder:level(hs.canvas.windowLevels.overlay):behavior({ "canJoinAllSpaces", "stationary" }):show()
end

-- 来源取监视区域所在的窗口，而不是按快捷键时最前面的窗口
local function watchContextFor(r)
  local w = windowAtRegion(r)
  local ctx = w and getContext(w) or getContext()
  ctx.win = nil
  return ctx
end

local function setWatchRegion(r)
  watchRegion = r
  watchCtx = watchContextFor(r)
  hs.http.post(SERVER .. "/api/watch/start", "{}", { ["Content-Type"] = "application/json" })   -- 换区域后重新比较
  showWatchBorder()
end

-- 用 Snipaste 框选一块新区域作为监视区域（框完按回车/⌘C/双击）
local function pickWatchRegion()
  snipToFile({}, 180, function(path)
    if not path then return end
    os.remove(path)
    local r = snipasteLastRegion()
    if r then
      setWatchRegion(r)
      hs.alert.show(string.format("监视区域已更新：%d×%d", r.w, r.h))
    end
  end)
end

local function watchTick()
  if not watching then return end
  if panel then hs.timer.doAfter(1, watchTick) return end   -- 有面板开着就先不截
  watchShot(function(path)
    if not watching then if path then os.remove(path) end return end
    if not path then hs.timer.doAfter(watchInterval(), watchTick) return end
    local p = payloadFrom(watchCtx)
    p.kind, p.image_path = "image", path
    hs.http.asyncPost(SERVER .. "/api/watch/frame", hs.json.encode(p), { ["Content-Type"] = "application/json" },
      function(code, body)
        local res = code == 200 and hs.json.decode(body) or {}
        if res.id and panel then
          -- 用户正在用面板收别的卡片，不打断：这张直接保存
          watchCount = watchCount + 1
          hs.alert.show("已自动保存第 " .. watchCount .. " 张（面板占用中）", 0.8)
          hs.timer.doAfter(watchInterval(), watchTick)
        elseif res.id and hs.settings.get("papercards.watchAutoSave") then
          -- 换页了，免确认模式：直接保存，继续监视
          watchCount = watchCount + 1
          hs.alert.show("已自动保存第 " .. watchCount .. " 张", 0.8)
          hs.timer.doAfter(watchInterval(), watchTick)
        elseif res.id then
          -- 换页了：弹出录入面板，面板关掉之后再继续监视
          watchCount = watchCount + 1
          local front = hs.window.focusedWindow()
          showPanel(res.id, front)
          panelClosedHook = function() if watching then hs.timer.doAfter(watchInterval(), watchTick) end end
        else
          hs.timer.doAfter(res.confirm and CONFIRM_DELAY or watchInterval(), watchTick)
        end
        if watchMenu then watchMenu:setTitle("📷 " .. watchCount) end
      end)
  end)
end

local function setWatchInterval(sec)
  hs.settings.set("papercards.watchInterval", sec)
  hs.alert.show("自动截图：每 " .. sec .. " 秒检查一次")
end

local function watchMenuItems()
  local cur = watchInterval()
  local items = {
    { title = "正在自动截图，已截 " .. watchCount .. " 张", disabled = true },
    { title = "-" },
    { title = watchRegion and string.format("监视区域：(%d, %d) %d×%d", watchRegion.x, watchRegion.y, watchRegion.w, watchRegion.h)
        or "监视区域：未设置", disabled = true },
    { title = "    用 Snipaste 重新框选监视区域…", fn = pickWatchRegion },
    { title = "来源：" .. ((watchCtx and (watchCtx.page_title or watchCtx.window_title)) or "未知"):sub(1, 60), disabled = true },
    { title = "    重新识别来源（取区域所在窗口）", fn = function()
        if watchRegion then watchCtx = watchContextFor(watchRegion) end
        hs.alert.show("来源：" .. tostring(watchCtx and (watchCtx.page_title or watchCtx.window_title)))
      end },
    { title = "    显示区域边框", checked = watchBorder ~= nil, fn = function()
        if watchBorder then watchBorder:delete(); watchBorder = nil; hs.settings.set("papercards.watchBorder", false)
        else hs.settings.set("papercards.watchBorder", true); showWatchBorder() end
      end },
    { title = "-" },
    { title = "检查间隔", disabled = true },
  }
  local presets, isPreset = { 2, 3, 5, 10, 15, 30 }, false
  for _, sec in ipairs(presets) do
    if sec == cur then isPreset = true end
    items[#items + 1] = { title = "    " .. sec .. " 秒", checked = sec == cur, fn = function() setWatchInterval(sec) end }
  end
  items[#items + 1] = {
    title = "    自定义…" .. (isPreset and "" or "（当前 " .. cur .. " 秒）"), checked = not isPreset,
    fn = function()
      hs.focus()
      local btn, txt = hs.dialog.textPrompt("自动截图检查间隔", "每隔多少秒检查一次（可以是小数）", tostring(cur), "确定", "取消")
      local sec = tonumber(txt)
      if btn == "确定" and sec and sec >= 0.5 then setWatchInterval(sec) end
    end,
  }
  items[#items + 1] = { title = "-" }
  local auto = hs.settings.get("papercards.watchAutoSave") or false
  items[#items + 1] = {
    title = "直接保存，不弹面板确认", checked = auto,
    fn = function()
      hs.settings.set("papercards.watchAutoSave", not auto)
      hs.alert.show(not auto and "换页后直接保存" or "换页后弹出面板确认")
    end,
  }
  items[#items + 1] = { title = "-" }
  items[#items + 1] = { title = "停止自动截图（⌃⌥⌘A）", fn = function() M.toggleWatch() end }
  return items
end

function M.toggleWatch()
  if watching then
    watching = false
    if watchMenu then watchMenu:delete(); watchMenu = nil end
    if watchBorder then watchBorder:delete(); watchBorder = nil end
    panelClosedHook = nil
    hs.alert.show("自动截图已停止，共 " .. watchCount .. " 张")
    return
  end
  if not ensureServer() then return end
  local region = snipasteLastRegion()
  if not region then
    hs.alert.show("先用 Snipaste 框选一次要监视的区域")
    return
  end
  watching, watchCount = true, 0
  watchRegion = region
  watchCtx = watchContextFor(region)
  hs.http.post(SERVER .. "/api/watch/start", "{}", { ["Content-Type"] = "application/json" })
  if hs.settings.get("papercards.watchBorder") ~= false then showWatchBorder() end
  watchMenu = hs.menubar.new()
  watchMenu:setTitle("📷 0")
  watchMenu:setTooltip("PaperCards 自动截图中，点击设置间隔或停止")
  watchMenu:setMenu(watchMenuItems)
  local silent = hs.screenRecordingState()
  hs.alert.show("开始自动截图，每 " .. watchInterval() .. " 秒检查一次\n来源：" .. tostring(watchCtx.page_title or watchCtx.window_title)
    .. (hs.settings.get("papercards.watchAutoSave") and "，直接保存" or "，弹面板确认")
    .. (silent and "" or "（未获屏幕录制权限，会闪屏）"))
  watchTick()
end

-- 把剪贴板里的内容收进来（配合 Snipaste 等截图工具：截图复制后按这个键）
function M.capturePasteboard()
  if not ensureServer() then return end
  local ctx = getContext()
  local p = payloadFrom(ctx)
  -- 20 秒内刚复制的截图优先；否则有选中的文字就收文字（避免收到剪贴板里残留的旧截图）
  local freshImage = hs.timer.secondsSinceEpoch() - lastImageAt < 20 and hs.pasteboard.readImage()
  local sel
  if not freshImage then
    sel = ctx.selection
    if not sel or sel:match("^%s*$") then sel = copySelection() end
  end
  local img = freshImage or ((not sel or sel:match("^%s*$")) and hs.pasteboard.readImage())
  if sel and not sel:match("^%s*$") then
    p.kind, p.text = "text", sel
  elseif img then
    local path = string.format("%s/papercards-%d.png", hs.fs.temporaryDirectory(), os.time())
    img:saveToFile(path, "png")
    p.kind, p.image_path = "image", path
  else
    local text = hs.pasteboard.getContents()
    if not text or text:match("^%s*$") then
      hs.alert.show("剪贴板是空的")
      return
    end
    p.kind, p.text = "text", text
  end
  post(p, ctx.win)
end

function M.openWall()
  if ensureServer() then hs.urlevent.openURL(SERVER) end
end

-- 包一层：记录每次按键，出错时弹提示，方便排查
local function guarded(name, fn)
  return function()
    print("PaperCards: " .. name .. " pressed, front=" .. tostring(hs.application.frontmostApplication():name()))
    local ok, err = xpcall(fn, debug.traceback)
    if not ok then
      print("PaperCards error: " .. tostring(err))
      hs.alert.show("PaperCards 出错：" .. tostring(err):sub(1, 120), 4)
    end
  end
end

-- 收集类快捷键：按下立即提示；收集完成（面板弹出）前重复按会被忽略，避免建出多张相同的卡片
local function guardedCapture(name, fn, async)
  local run = guarded(name, fn)
  return function()
    local now = hs.timer.secondsSinceEpoch()
    if captureBusyAt and now - captureBusyAt < 6 then
      hs.alert.show("正在收集，请稍候", 0.8)
      return
    end
    captureBusyAt = now
    hs.alert.show("收集中…", 0.6)
    local before = postCount
    run()
    -- 同步流程没有发出保存（没选中文字、剪贴板为空、出错）→ 立刻解除；异步截图由回调解除
    if not async and postCount == before then captureBusyAt = nil end
  end
end

hs.hotkey.bind(MODS, "c", guardedCapture("C", M.captureText))
hs.hotkey.bind(MODS, "s", guarded("S", M.captureScreenshot))
hs.hotkey.bind(MODS, "r", guardedCapture("R", M.captureLastRegion, true))
hs.hotkey.bind(MODS, "v", guardedCapture("V", M.capturePasteboard))
hs.hotkey.bind(MODS, "a", guarded("A", M.toggleWatch))
hs.hotkey.bind(MODS, "p", guarded("P", M.openWall))
M._showPanel = showPanel   -- 供调试 / 测试
M._panel = function() return panel end
hs.timer.doAfter(1, function() if not serverUp() then ensureServer() end end)

return M
