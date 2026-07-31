# PLAN_A4 —— 通知与转发「出口类型锁死 Discord」（施工图）

> 给实现窗口（Kimi 等快模型）：**只照本文件改，别自由发挥。** 只动 `ui.html` 一个文件，
> 服务端、扩展、测试脚本都不碰。行号以本 PLAN 提交时的 ui.html 为准（2637 行版）。

## 0. 根因（Claude 窗口已实测定性，实现方不用再查）

出口类型选择器**功能上没坏**：演示模式合成事件、真鼠标+键盘、真服务端 curl 全链路都验过，
选飞书加出来就是飞书，两种类型混存正常（norm_hook / merge_hooks 不改类型）。

坏的是**交互设计**，三处叠加让用户必然误解成「锁死 Discord」：

1. 类型下拉 `#hookPreset` 没有标签，还排在「＋ 添加出口」按钮**后面**（ui.html:630-643），
   看起来像个无关控件；且它记住上次的选择——用户选过一次「Discord 频道」，之后每次点
   「＋添加出口」都复制出 Discord 配置。
2. 已添加的出口行内**没有类型选择器**，想把一条出口
   从 Discord 换成飞书/微信找不到入口 →「其他方式选不了」。（行内只有 方式 POST/GET
   和 请求体格式 两个下拉，都不是「服务类型」。）
3. 模板预填的地址（`把机器人的key填在这里`）在密码框里显示成一排点（ui.html:1940），
   用户看不见提示文字，每条出口看起来长得都一样 → 全像 Discord。

## 1. 改动一：添加行重排 + 加说明（ui.html 629-646）

把 629-647 的那个 `.row` 改成（下拉挪到按钮**前面**，加引导文字，按钮文案缩短）：

```html
          <div class="row" style="border-top:1px solid var(--line);padding-top:14px">
            <span class="hint">加一条出口，先选类型：</span>
            <select id="hookPreset" title="选好类型再点「＋ 添加」，按该服务的格式套好模板">
              <option value="json">通用 JSON</option>
              <option value="discord">Discord 频道</option>
              <option value="wecom">企业微信机器人</option>
              <option value="serverchan">Server酱 → 微信</option>
              <option value="feishu">飞书机器人</option>
              <option value="dingtalk">钉钉机器人</option>
              <option value="telegram">Telegram</option>
              <option value="text">纯文本</option>
              <option value="form">表单（key=value）</option>
              <option value="get">GET 查询串</option>
              <option value="raw">原始 JSON（整条消息）</option>
            </select>
            <button class="btn" id="btnAddHook">＋ 添加</button>
            <span class="sp" style="flex:1"></span>
            <button class="btn pri" id="btnSaveOut">保存出口</button>
            <button class="btn" data-test="all">全部测一遍</button>
          </div>
```

（选项列表原样保留，只是位置、标签、title 变了。）

## 2. 改动二：出口行内加「类型」下拉，可把现有出口换成别的服务

### 2a. HOOK_PRESETS 后面（1922 行 `};` 之后）加两个常量 + 一个函数

```js
const HOOK_PRESET_LABELS={json:'通用 JSON',discord:'Discord 频道',wecom:'企业微信机器人',
  serverchan:'Server酱 → 微信',feishu:'飞书机器人',dingtalk:'钉钉机器人',telegram:'Telegram',
  text:'纯文本',form:'表单（key=value）',get:'GET 查询串',raw:'原始 JSON（整条消息）'};
const TPL_URL=/把.+?填在这里|example\.com/;   // 地址还是模板占位、没填真值
function applyPreset(h,k){
  const p=HOOK_PRESETS[k];if(!p)return;
  h.name=p.name;h.method=p.method||'POST';h.content=p.content||'json';h.body=p.body||'';
  if(p.url!==undefined)h.url=p.url;                 // 该服务有地址模板 → 换上
  else if(TPL_URL.test(h.url||''))h.url='';         // 旧地址还是别家的模板 → 清掉
  h.verified=false;                                 // 换了类型，测通状态作废
}
```

### 2b. renderHooks 的行模板（1929-1938 的 grid3）改成 4 个字段，「类型」放最前

把 1930-1938 的 `<div class="grid3">…</div>` 换成：

```js
      <div class="grid3">
        <label class="f"><span>类型 <em>换类型会按模板重填</em></span><select data-htpl="${i}">
          <option value="">${esc(HOOK_PRESET_LABELS[h.tpl]||'自定义')}</option>
          ${Object.entries(HOOK_PRESET_LABELS).map(([k,n])=>`<option value="${k}">${n}</option>`).join('')}
        </select></label>
        <label class="f"><span>名称</span><input type="text" data-h="${i}" data-k="name" value="${esc(h.name||'')}"></label>
        <label class="f"><span>方式</span><select data-h="${i}" data-k="method">
          <option ${get?'':'selected'}>POST</option><option ${get?'selected':''}>GET</option></select></label>
        <label class="f"><span>请求体格式</span><select data-h="${i}" data-k="content" ${get?'disabled':''}>
          <option value="json" ${h.content==='json'?'selected':''}>JSON</option>
          <option value="form" ${h.content==='form'?'selected':''}>表单</option>
          <option value="text" ${h.content==='text'?'selected':''}>纯文本</option></select></label>
      </div>
```

说明：grid3 放 4 个 label 会折行，接受（别去改 CSS）。第一个 option 是当前状态的展示位
（value=''，选回它不触发任何事）。`h.tpl` 是纯前端字段，服务端 norm_hook 会丢掉它、
不落库，**不要去改 server.py 的 HOOK_FIELDS**——每次读回来显示「自定义」没关系，
名称栏已经能认出是谁。

### 2c. btnAddHook（1964-1967）记下 tpl

1966 行 push 的对象里加一项 `tpl:$('#hookPreset').value`：

```js
  hooks().push({id:'n'+Math.random().toString(16).slice(2,10),url:'',headers:'',enabled:true,verified:false,...p,tpl:$('#hookPreset').value});
```

（注意 `tpl` 放在 `...p` 之后，preset 里没有 tpl 字段，纯保险。）

### 2d. 绑定行内类型下拉（1955-1961 的绑定区，加一段）

在 `$$('#hookList [data-hookrm]')…` 那行**前面**插入：

```js
  $$('#hookList [data-htpl]').forEach(e=>e.onchange=()=>{
    const k=e.value,h=hooks()[+e.dataset.htpl];if(!k||!h)return;
    if(!confirm(`把这条出口按「${HOOK_PRESET_LABELS[k]}」模板重填？名称和请求体会被替换。`)){renderHooks();return;}
    applyPreset(h,k);h.tpl=k;renderHooks();
    toast('已按模板重填，检查地址后点「测试」');
  });
```

## 3. 改动三：地址还是模板占位时不打码（1939-1941）

把 1940-1941 的地址 input 改成按内容决定初始 type（占位提示要让人看见）：

```js
      <label class="f"><span>地址 <em>URL 里也能写占位符</em></span>
        <span class="ipw"><input type="${TPL_URL.test(h.url||'')?'text':'password'}" data-h="${i}" data-k="url" value="${esc(h.url||'')}"
          placeholder="https://…"><button class="eye" type="button">${TPL_URL.test(h.url||'')?'🙈':'👁'}</button></span></label>
```

bindEyes（2004-2008）按当前 type 切换，不用改。

## 4. 改动四：帮助文案（2189 行「三步」第一条）

把 `<li>选个模板（通用 JSON / Discord 频道 / 纯文本 / 表单 / GET）→「＋ 添加出口」</li>` 换成：

```html
    <li>底部先选类型（Discord / 企业微信 / 飞书 / Telegram…）→「＋ 添加」；已加的出口想换服务，用行内「类型」下拉按模板重填</li>
```

## 5. 验收（实现方自己跑完再交）

1. **演示模式目检**（不起服务端，直接浏览器开 ui.html，进「通知与转发」）：
   - 底部一行顺序：说明文字 → 类型下拉 → ＋添加 → （弹簧）→ 保存出口 → 全部测一遍；
   - 下拉选「飞书机器人」点＋添加 → 新行名称=飞书，**地址明文**显示 `…把机器人的token填在这里`，
     眼睛图标是 🙈；
   - 该行「类型」下拉换「Telegram」→ confirm → 名称/请求体/地址全变 Telegram 模板，
     「还没测通」标签在；点取消的话行不变；
   - 下拉选「Discord 频道」点＋添加 → 名称=Discord 频道、地址空、密码框（👁）；
   - 名称手改过再换类型 → 名称被模板覆盖（预期行为，confirm 里说了）。
2. **回归**（server.py 没动，纯保险）：照 `tests/RUN.md` 跑全套，应仍是 500/500 全绿 +
   `content_test.mjs` 46/16 全绿。
3. 不加新 e2e（无服务端行为变化）；不 bump 版本、不提 EXT_MIN（没动 extension）。

## 6. 完工动作

- BACKLOG.md A4 打勾（注明「UX 重做：类型下拉前置+行内换类型+模板地址明文」）；
- NOW.md 状态改成「A4 施工完成，下一件 Claude 窗口 review」；
- save.py 存盘推送。
