# =============================================================================
# DESKTOP_UI/hub_tk.py
# Universal MCP Desktop Client — Tkinter (stdlib only), dark/light theme
# Universal MCP Hub (Sandboxed) - based on PyFundaments Architecture
# Copyright 2026 - Volkan Kücükbudak
# Apache License V. 2 + ESOL 1.1
# =============================================================================
# USAGE:  python hub_tk.py        (Linux: apt install python3-tk)
# SETUP:  Settings → Hub URL (+ HF token for private Space, Hub token if the hub
#         gate is on) → Save → Connect
# =============================================================================
import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import hub_core as core

DARK = dict(bg="#14161a", panel="#1c1f26", field="#232733", fg="#e6e8ec", muted="#8b93a1",
            accent="#5b9cff", border="#2c3140", user="#7dd3a8", err="#ff7b7b", sel="#2f4a7a")
LIGHT = dict(bg="#f4f5f7", panel="#e9ebef", field="#ffffff", fg="#1b1f27", muted="#667085",
             accent="#2563eb", border="#c9ced6", user="#157347", err="#c0262d", sel="#bcd3ff")
PALETTES = {"dark": DARK, "light": LIGHT}
MAX_LOG_LINES = 500


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Universal MCP Hub")
        self.geometry("980x700")
        self.minsize(760, 520)
        self.cfg = core.load_config()
        self.chats = core.load_chats()
        self.chat_id = ""
        self._q = queue.Queue()
        self._combos, self._texts = [], []
        self._attached = None
        self._chat_ids = []

        self.f_ui = tkfont.nametofont("TkDefaultFont")
        self.f_mono = tkfont.nametofont("TkFixedFont")

        self._build()
        self.apply_theme()
        if not self.chats:
            core.new_chat(self.chats)
        self._refresh_chats(select_last=True)
        self.after(100, self._poll)
        if self.cfg["hub_url"]:
            self.after(300, self._connect)

    # ── background work (Tk is not thread-safe: results go through a queue) ──
    def _bg(self, fn, on_ok, on_err=None):
        def run():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001 — surfaced to the user
                self._q.put((on_err or self._show_error, e))
            else:
                self._q.put((on_ok, res))
        threading.Thread(target=run, daemon=True).start()

    def _poll(self):
        try:
            while True:
                cb, arg = self._q.get_nowait()
                try:
                    cb(arg)
                except Exception as e:  # noqa: BLE001
                    self._log(f"UI callback failed: {type(e).__name__}: {e}")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _client(self) -> core.HubClient:
        return core.HubClient(self.cfg["hub_url"], self.cfg["hf_token"], self.cfg["hub_token"])

    def _show_error(self, e):
        self._log(f"error: {e}")
        self._set_status(f"✗ {e}", error=True)

    # ── layout ───────────────────────────────────────────────────────────────
    def _build(self):
        head = ttk.Frame(self, padding=(12, 8))
        head.pack(fill="x")
        ttk.Label(head, text="⬡ Universal MCP Hub", style="Title.TLabel").pack(side="left")
        self.theme_btn = ttk.Button(head, text="◐", width=3, command=self._toggle_theme)
        self.theme_btn.pack(side="right", padx=(6, 0))
        ttk.Button(head, text="Connect", style="Accent.TButton", command=self._connect).pack(side="right")
        self.status_var = tk.StringVar(value="○ not connected")
        self.status_lbl = ttk.Label(head, textvariable=self.status_var, style="Muted.TLabel")
        self.status_lbl.pack(side="right", padx=12)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        for name, builder in (("Chat", self._tab_chat), ("Status", self._tab_status),
                              ("Skills", self._tab_skills), ("Settings", self._tab_settings),
                              ("Logs", self._tab_logs)):
            frame = ttk.Frame(self.nb, padding=10)
            self.nb.add(frame, text=f" {name} ")
            builder(frame)

    def _combo(self, parent, var, values, width=18, readonly=True):
        cb = ttk.Combobox(parent, textvariable=var, values=values, width=width,
                          state="readonly" if readonly else "normal")
        self._combos.append(cb)
        return cb

    def _text(self, parent, height=10):
        box = ttk.Frame(parent)
        txt = tk.Text(box, height=height, wrap="word", relief="flat", borderwidth=0,
                      highlightthickness=1, padx=8, pady=6, state="disabled", font=self.f_mono)
        sb = ttk.Scrollbar(box, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        self._texts.append(txt)
        return box, txt

    @staticmethod
    def _set_text(txt, content):
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        txt.insert("1.0", content)
        txt.configure(state="disabled")

    # ── Chat tab ─────────────────────────────────────────────────────────────
    def _tab_chat(self, f):
        row = ttk.Frame(f)
        row.pack(fill="x")
        self.tool_var = tk.StringVar(value=self.cfg["default_tool"])
        self.provider_var = tk.StringVar(value="default")
        self.model_var = tk.StringVar(value="default")
        self.tokens_var = tk.IntVar(value=1024)
        for label, widget in (
            ("Tool", self._combo(row, self.tool_var, core.KNOWN_TOOLS)),
            ("Provider", self._combo(row, self.provider_var, ["default"], 14)),
            ("Model", self._combo(row, self.model_var, ["default"], 30, readonly=False)),
        ):
            ttk.Label(row, text=label, style="Muted.TLabel").pack(side="left", padx=(0, 4))
            widget.pack(side="left", padx=(0, 10))
        self.tool_cb, self.provider_cb, self.model_cb = self._combos[0], self._combos[1], self._combos[2]
        ttk.Label(row, text="Max tokens", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        ttk.Spinbox(row, from_=64, to=8192, increment=64, width=6, textvariable=self.tokens_var).pack(side="left")

        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=(8, 6))
        self.chat_var = tk.StringVar()
        self.chat_cb = self._combo(row2, self.chat_var, [], 38)
        self.chat_cb.pack(side="left")
        self.chat_cb.bind("<<ComboboxSelected>>", self._on_chat_selected)
        ttk.Button(row2, text="New", command=self._new_chat).pack(side="left", padx=6)
        ttk.Button(row2, text="Delete", command=self._delete_chat).pack(side="left")

        box, self.chat_text = self._text(f)
        box.pack(fill="both", expand=True)

        bottom = ttk.Frame(f)
        bottom.pack(fill="x", pady=(8, 0))
        ttk.Button(bottom, text="Attach", command=self._attach).pack(side="left")
        self.file_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.file_var, style="Muted.TLabel").pack(side="left", padx=6)
        ttk.Button(bottom, text="Send", style="Accent.TButton", command=self._send).pack(side="right")
        self.entry = ttk.Entry(bottom)
        self.entry.pack(side="left", fill="x", expand=True, padx=6)
        self.entry.bind("<Return>", lambda _e: self._send())

    def _refresh_chats(self, select_last=False):
        self._chat_ids = list(self.chats)
        self.chat_cb["values"] = [self.chats[c]["title"] for c in self._chat_ids]
        if select_last or self.chat_id not in self.chats:
            self.chat_id = self._chat_ids[-1]
        self.chat_var.set(self.chats[self.chat_id]["title"])
        self._render_chat()

    def _on_chat_selected(self, _e=None):
        idx = self.chat_cb.current()
        if 0 <= idx < len(self._chat_ids):
            self.chat_id = self._chat_ids[idx]
            self._render_chat()

    def _new_chat(self):
        self.chat_id = core.new_chat(self.chats)
        core.save_chats(self.chats)
        self._refresh_chats()

    def _delete_chat(self):
        if not messagebox.askyesno("Delete chat", "Delete this chat permanently?"):
            return
        self.chats.pop(self.chat_id, None)
        if not self.chats:
            core.new_chat(self.chats)
        core.save_chats(self.chats)
        self._refresh_chats(select_last=True)

    def _render_chat(self):
        self.chat_text.configure(state="normal")
        self.chat_text.delete("1.0", "end")
        self.chat_text.configure(state="disabled")
        for m in self.chats[self.chat_id]["messages"]:
            self._put(m["role"], m["text"], m.get("tool", ""))

    def _put(self, role, text, tool=""):
        label = {"user": f"▶ You [{tool}]" if tool else "▶ You", "hub": "⬡ Hub",
                 "error": "✗ Error", "info": "·"}.get(role, role)
        t = self.chat_text
        t.configure(state="normal")
        t.insert("end", label + "\n", (role + "_h",))
        t.insert("end", text.rstrip() + "\n\n", (role,))
        t.configure(state="disabled")
        t.see("end")

    def _append(self, cid, role, text, tool=""):
        chat = self.chats.get(cid)
        if chat is None:  # chat deleted while the request was running
            return
        chat["messages"].append({"role": role, "text": text, "tool": tool})
        retitled = role == "user" and chat["title"].startswith("Chat ")
        if retitled:
            chat["title"] = text.strip().replace("\n", " ")[:40] or chat["title"]
        core.save_chats(self.chats)
        if retitled:
            self._refresh_chats()  # re-renders the whole chat, new message included
        elif cid == self.chat_id:
            self._put(role, text, tool)

    def _attach(self):
        path = filedialog.askopenfilename(title="Attach text file")
        if not path:
            return
        try:
            self._attached = (path.replace("\\", "/").rsplit("/", 1)[-1], core.read_text_file(path))
            self.file_var.set(f"📎 {self._attached[0]}")
        except (core.HubError, OSError) as e:
            self._attached = None
            self.file_var.set("")
            messagebox.showerror("Attach", str(e))

    def _send(self):
        prompt = self.entry.get().strip()
        if not prompt:
            return
        tool = self.tool_var.get() or "llm_complete"
        provider = self.provider_var.get()
        if provider == "default":
            provider = self.cfg["default_provider"]
        model = self.model_var.get().strip()
        if model in ("", "default"):
            model = self.cfg["default_model"]
        try:
            max_tokens = int(self.tokens_var.get())
        except (tk.TclError, ValueError):
            max_tokens = 1024

        full = prompt
        if self._attached:
            full = f"{prompt}\n\n[File: {self._attached[0]}]\n{self._attached[1]}"
            self._attached = None
            self.file_var.set("")
        cid = self.chat_id
        self._append(cid, "user", prompt, tool)
        self.entry.delete(0, "end")
        self._log(f"→ {tool} provider={provider or 'default'} model={model or 'default'}")
        params = core.build_params(tool, full, provider, model, max_tokens)
        self._bg(lambda: self._client().tool(tool, params, timeout=120),
                 lambda r: self._append(cid, "hub", core.format_result(r)),
                 lambda e: self._append(cid, "error", str(e)))

    # ── Status tab ───────────────────────────────────────────────────────────
    def _tab_status(self, f):
        self.group_var = tk.StringVar(value="model")
        self.days_var = tk.StringVar(value="")
        self.cat_query = tk.StringVar(value="")
        self.cat_free = tk.BooleanVar(value=True)
        self.cat_limit = tk.StringVar(value="40")
        self.price_model = tk.StringVar(value="")

        r1 = ttk.Frame(f)
        r1.pack(fill="x")
        for label, tool in (("Health", "health_check"), ("Active tools", "list_active_tools"),
                            ("Persist queue", "persist_status")):
            ttk.Button(r1, text=label, command=lambda t=tool: self._run_into(self.status_out, t, {})
                       ).pack(side="left", padx=(0, 6))

        r2 = ttk.Frame(f)
        r2.pack(fill="x", pady=(8, 0))
        ttk.Button(r2, text="Usage", command=self._usage).pack(side="left", padx=(0, 6))
        self._combo(r2, self.group_var, ["model", "provider", "tool", "day"], 9).pack(side="left", padx=(0, 6))
        ttk.Label(r2, text="days", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        ttk.Entry(r2, textvariable=self.days_var, width=5).pack(side="left")

        r3 = ttk.Frame(f)
        r3.pack(fill="x", pady=(8, 0))
        ttk.Button(r3, text="OpenRouter catalog", command=self._catalog).pack(side="left", padx=(0, 6))
        ttk.Entry(r3, textvariable=self.cat_query, width=24).pack(side="left", padx=(0, 6))
        ttk.Checkbutton(r3, text="free only", variable=self.cat_free).pack(side="left", padx=(0, 6))
        ttk.Label(r3, text="limit", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        ttk.Entry(r3, textvariable=self.cat_limit, width=5).pack(side="left")

        r4 = ttk.Frame(f)
        r4.pack(fill="x", pady=(8, 8))
        ttk.Button(r4, text="Model pricing", command=self._pricing).pack(side="left", padx=(0, 6))
        ttk.Entry(r4, textvariable=self.price_model, width=40).pack(side="left")

        box, self.status_out = self._text(f)
        box.pack(fill="both", expand=True)

    def _run_into(self, out, tool, params):
        self._set_text(out, "…")
        self._bg(lambda: self._client().tool(tool, params, timeout=30),
                 lambda r: self._set_text(out, core.format_result(r)),
                 lambda e: self._set_text(out, f"✗ {e}"))

    def _usage(self):
        params = {"group_by": self.group_var.get()}
        if self.days_var.get().strip().isdigit():
            params["days"] = int(self.days_var.get())
        self._run_into(self.status_out, "usage_stats", params)

    def _catalog(self):
        limit = int(self.cat_limit.get()) if self.cat_limit.get().strip().isdigit() else 40
        self._run_into(self.status_out, "openrouter_catalog",
                       {"query": self.cat_query.get().strip(), "free_only": bool(self.cat_free.get()), "limit": limit})

    def _pricing(self):
        name = self.price_model.get().strip()
        if not name:
            self._set_text(self.status_out, "✗ model name required")
            return
        self._run_into(self.status_out, "get_model_pricing", {"model_name": name})

    # ── Skills tab ───────────────────────────────────────────────────────────
    def _tab_skills(self, f):
        self.scope_var = tk.StringVar(value="all")
        self.tag_var = tk.StringVar()
        self.query_var = tk.StringVar()
        self.topk_var = tk.IntVar(value=3)
        row = ttk.Frame(f)
        row.pack(fill="x")
        ttk.Label(row, text="Scope", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        self._combo(row, self.scope_var, ["all", "user", "agent"], 8).pack(side="left", padx=(0, 10))
        ttk.Label(row, text="Tag", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        ttk.Entry(row, textvariable=self.tag_var, width=14).pack(side="left", padx=(0, 10))
        ttk.Label(row, text="Top-k", style="Muted.TLabel").pack(side="left", padx=(0, 4))
        ttk.Spinbox(row, from_=1, to=10, width=4, textvariable=self.topk_var).pack(side="left")
        ttk.Button(row, text="Catalog", command=self._skills_catalog).pack(side="right")

        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=8)
        q = ttk.Entry(row2, textvariable=self.query_var)
        q.pack(side="left", fill="x", expand=True, padx=(0, 6))
        q.bind("<Return>", lambda _e: self._skills_search())
        ttk.Button(row2, text="Search", style="Accent.TButton", command=self._skills_search).pack(side="left")

        box, self.skills_out = self._text(f)
        box.pack(fill="both", expand=True)

    def _skills_catalog(self):
        params = {"scope": self.scope_var.get()}
        if self.tag_var.get().strip():
            params["tag"] = self.tag_var.get().strip().lower()
        self._run_into(self.skills_out, "show_skills", params)

    def _skills_search(self):
        q, tag = self.query_var.get().strip(), self.tag_var.get().strip().lower()
        if not q and not tag:
            self._set_text(self.skills_out, "✗ query or tag required")
            return
        try:
            top_k = max(1, min(10, int(self.topk_var.get())))
        except (tk.TclError, ValueError):
            top_k = 3
        params = {"prompt": q, "scope": self.scope_var.get(), "top_k": top_k}
        if tag:
            params["tag"] = tag
        self._run_into(self.skills_out, "search_skills", params)

    # ── Settings tab ─────────────────────────────────────────────────────────
    def _tab_settings(self, f):
        self.set_vars = {k: tk.StringVar(value=str(self.cfg[k])) for k in
                         ("hub_url", "hf_token", "hub_token", "default_tool", "default_provider",
                          "default_model", "font_size", "theme")}
        self.show_tokens = tk.BooleanVar(value=False)
        self._token_entries = []
        rows = (("Hub URL", "hub_url", False), ("HF token (private Space)", "hf_token", True),
                ("Hub token (X-Hub-Token)", "hub_token", True), ("Default tool", "default_tool", False),
                ("Default provider", "default_provider", False), ("Default model", "default_model", False),
                ("Font size", "font_size", False))
        f.columnconfigure(1, weight=1)
        for i, (label, key, secret) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=i, column=0, sticky="w", pady=4, padx=(0, 12))
            e = ttk.Entry(f, textvariable=self.set_vars[key], show="•" if secret else "")
            e.grid(row=i, column=1, sticky="ew", pady=4)
            if secret:
                self._token_entries.append(e)
        n = len(rows)
        ttk.Label(f, text="Theme").grid(row=n, column=0, sticky="w", pady=4)
        self._combo(f, self.set_vars["theme"], ["dark", "light"], 10).grid(row=n, column=1, sticky="w", pady=4)
        ttk.Checkbutton(f, text="show tokens", variable=self.show_tokens, command=self._toggle_tokens
                        ).grid(row=n + 1, column=1, sticky="w", pady=4)
        ttk.Button(f, text="Save", style="Accent.TButton", command=self._save_settings
                   ).grid(row=n + 2, column=1, sticky="w", pady=10)
        ttk.Label(f, text=f"Stored in {core.CONFIG_FILE}", style="Muted.TLabel"
                  ).grid(row=n + 3, column=0, columnspan=2, sticky="w")

    def _toggle_tokens(self):
        for e in self._token_entries:
            e.configure(show="" if self.show_tokens.get() else "•")

    def _save_settings(self):
        v = {k: var.get().strip() for k, var in self.set_vars.items()}
        v["hub_url"] = core.normalize_url(v["hub_url"])
        try:
            v["font_size"] = max(8, min(24, int(v["font_size"])))
        except ValueError:
            v["font_size"] = self.cfg["font_size"]
        if v["theme"] not in PALETTES:
            v["theme"] = "dark"
        if core.is_insecure(v["hub_url"]):
            messagebox.showwarning("Insecure URL", "http:// to a remote host sends your tokens unencrypted. Use https://.")
        self.cfg.update(v)
        core.save_config(self.cfg)
        self.set_vars["hub_url"].set(v["hub_url"])
        self.set_vars["font_size"].set(str(v["font_size"]))
        self.apply_theme()
        self._set_status("settings saved")

    # ── Logs tab ─────────────────────────────────────────────────────────────
    def _tab_logs(self, f):
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Clear", command=lambda: self._set_text(self.log_text, "")).pack(side="left")
        box, self.log_text = self._text(f)
        box.pack(fill="both", expand=True)

    def _log(self, msg):
        t = self.log_text
        t.configure(state="normal")
        t.insert("end", f"{datetime.now():%H:%M:%S}  {msg}\n")
        lines = int(t.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            t.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        t.configure(state="disabled")
        t.see("end")

    # ── connect / status ─────────────────────────────────────────────────────
    def _set_status(self, text, error=False):
        self.status_var.set(text)
        self.status_lbl.configure(style="Err.TLabel" if error else "Muted.TLabel")

    def _connect(self):
        self._set_status("… connecting")

        def work():
            client = self._client()
            health = client.health()
            try:
                return health, client.tool("list_active_tools", timeout=20), ""
            except core.HubError as e:
                return health, None, str(e)

        self._bg(work, self._on_connected)

    def _on_connected(self, res):
        health, info, tools_err = res
        up = health.get("uptime_seconds", "?") if isinstance(health, dict) else "?"
        self._log(f"[health] {core.format_result(health)}")
        if info is None:
            self._set_status(f"● reachable (uptime {up}s) — {tools_err}", error=True)
            return
        self._set_status(f"● connected — uptime {up}s")
        tools = info.get("active_tools") or core.KNOWN_TOOLS
        self.tool_cb["values"] = tools
        if self.tool_var.get() not in tools:
            self.tool_var.set(tools[0])
        self.provider_cb["values"] = ["default"] + list(info.get("active_llm_providers", []))
        self.model_cb["values"] = ["default"] + list(info.get("available_models", []))
        self._log(f"[tools] {tools}")

    # ── theme ────────────────────────────────────────────────────────────────
    def _toggle_theme(self):
        self.cfg["theme"] = "light" if self.cfg["theme"] == "dark" else "dark"
        self.set_vars["theme"].set(self.cfg["theme"])
        core.save_config(self.cfg)
        self.apply_theme()

    def apply_theme(self):
        p = PALETTES.get(self.cfg["theme"], DARK)
        size = int(self.cfg["font_size"])
        self.f_ui.configure(size=size)
        self.f_mono.configure(size=size)
        s = ttk.Style(self)
        s.theme_use("clam")
        self.configure(bg=p["bg"])
        s.configure(".", background=p["bg"], foreground=p["fg"], fieldbackground=p["field"],
                    bordercolor=p["border"], lightcolor=p["bg"], darkcolor=p["bg"],
                    troughcolor=p["panel"], focuscolor=p["accent"], insertcolor=p["fg"])
        s.configure("TLabel", background=p["bg"], foreground=p["fg"])
        s.configure("Muted.TLabel", foreground=p["muted"])
        s.configure("Err.TLabel", foreground=p["err"])
        s.configure("Title.TLabel", font=(self.f_ui.actual("family"), size + 3, "bold"))
        s.configure("TButton", background=p["panel"], foreground=p["fg"], bordercolor=p["border"],
                    padding=(10, 4))
        s.map("TButton", background=[("active", p["border"]), ("pressed", p["border"])])
        s.configure("Accent.TButton", background=p["accent"], foreground="#ffffff")
        s.map("Accent.TButton", background=[("active", p["accent"]), ("pressed", p["accent"])])
        s.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"], insertcolor=p["fg"], padding=4)
        s.configure("TSpinbox", fieldbackground=p["field"], foreground=p["fg"], arrowcolor=p["muted"],
                    background=p["panel"], insertcolor=p["fg"])
        s.configure("TCombobox", fieldbackground=p["field"], foreground=p["fg"], background=p["panel"],
                    arrowcolor=p["muted"], selectbackground=p["field"], selectforeground=p["fg"], padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", p["field"])], foreground=[("readonly", p["fg"])],
              selectbackground=[("readonly", p["field"])], selectforeground=[("readonly", p["fg"])])
        s.configure("TCheckbutton", background=p["bg"], foreground=p["fg"])
        s.map("TCheckbutton", background=[("active", p["bg"])], indicatorcolor=[("selected", p["accent"])])
        s.configure("TNotebook", background=p["bg"], borderwidth=0)
        s.configure("TNotebook.Tab", background=p["panel"], foreground=p["muted"], padding=(14, 6), borderwidth=0)
        s.map("TNotebook.Tab", background=[("selected", p["bg"])], foreground=[("selected", p["fg"])])
        s.configure("Vertical.TScrollbar", background=p["panel"], troughcolor=p["bg"],
                    arrowcolor=p["muted"], bordercolor=p["bg"])
        self.option_add("*TCombobox*Listbox.background", p["field"])
        self.option_add("*TCombobox*Listbox.foreground", p["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", p["sel"])
        self.option_add("*TCombobox*Listbox.selectForeground", p["fg"])
        for cb in self._combos:  # popdown listboxes already exist after first use → restyle directly
            try:
                pd = self.tk.call("ttk::combobox::PopdownWindow", str(cb))
                self.tk.call(f"{pd}.f.l", "configure", "-background", p["field"], "-foreground", p["fg"],
                             "-selectbackground", p["sel"], "-selectforeground", p["fg"])
            except tk.TclError:
                pass
        for t in self._texts:
            t.configure(bg=p["field"], fg=p["fg"], insertbackground=p["fg"], selectbackground=p["sel"],
                        selectforeground=p["fg"], highlightbackground=p["border"], highlightcolor=p["border"])
        bold = (self.f_mono.actual("family"), size, "bold")
        t = self.chat_text
        for role, color in (("user", p["user"]), ("hub", p["fg"]), ("error", p["err"]), ("info", p["muted"])):
            t.tag_configure(role, foreground=color)
            t.tag_configure(role + "_h", foreground=color, font=bold)
        self._dark_titlebar(self.cfg["theme"] == "dark")

    def _dark_titlebar(self, dark):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            val = ctypes.c_int(1 if dark else 0)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (Win10 2004+ / older builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val), ctypes.sizeof(val)) == 0:
                    break
        except Exception:  # noqa: BLE001 — cosmetic only
            pass


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
