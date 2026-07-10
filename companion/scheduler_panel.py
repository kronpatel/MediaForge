from __future__ import annotations
import customtkinter as ctk
from base_page import BasePage
from schedule_dialog import ScheduleDialog
from tkinter import messagebox

# ACCENT COLORS
_CLR_BG         = "#0f1117"
_CLR_SURFACE    = "#1a1d27"
_CLR_CARD       = "#20232f"
_CLR_BORDER     = "#2e3347"
_CLR_TEXT       = "#e8eaf0"
_CLR_SUBTEXT    = "#8b92a8"
_CLR_ACCENT     = "#4f8ef7"
_CLR_ACCENT_HOV = "#3a76e8"

_CLR_GREEN      = "#22c55e"
_CLR_YELLOW     = "#f59e0b"
_CLR_RED        = "#ef4444"

STATE_COLORS = {
    "Scheduled": "#f59e0b",
    "Waiting": "#8b92a8",
    "Running": "#4f8ef7",
    "Completed": "#22c55e",
    "Failed": "#ef4444",
    "Cancelled": "#ef4444",
    "Expired": "#8b92a8"
}

class SchedulerRow(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkFrame, job: dict, on_click: callable, on_double_click: callable) -> None:
        super().__init__(
            master,
            fg_color="#1a1d27",
            border_color="#2e3347",
            border_width=1,
            corner_radius=8,
            height=40
        )
        self.job = job
        self.on_click = on_click
        self.on_double_click = on_double_click
        self.selected = False
        
        self._build_ui()
        self.bind("<Button-1>", lambda e: self._row_clicked())
        self.bind("<Double-Button-1>", lambda e: self.on_double_click(self.job["uuid"]))

    def _build_ui(self) -> None:
        # Prevent propagation
        self.pack_propagate(False)
        
        # 1. State badge
        state = self.job.get("state", "Scheduled")
        bg_col = STATE_COLORS.get(state, "#8b92a8")
        
        self.state_lbl = ctk.CTkLabel(
            self,
            text=state.upper(),
            font=ctk.CTkFont(family="Segoe UI", size=9, weight="bold"),
            text_color="#ffffff",
            fg_color=bg_col,
            corner_radius=4,
            width=80,
            height=20
        )
        self.state_lbl.pack(side="left", padx=(10, 5), pady=10)
        self.state_lbl.bind("<Button-1>", lambda e: self._row_clicked())

        # Enabled check indicator
        enabled = self.job.get("enabled", True)
        self.enabled_lbl = ctk.CTkLabel(
            self,
            text="●" if enabled else "○",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=_CLR_GREEN if enabled else _CLR_SUBTEXT,
            width=20
        )
        self.enabled_lbl.pack(side="left", padx=5)
        self.enabled_lbl.bind("<Button-1>", lambda e: self._row_clicked())

        # 2. URL / Title
        self.url_lbl = ctk.CTkLabel(
            self,
            text=self.job.get("url", ""),
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=_CLR_TEXT,
            anchor="w"
        )
        self.url_lbl.pack(side="left", fill="x", expand=True, padx=10)
        self.url_lbl.bind("<Button-1>", lambda e: self._row_clicked())

        # 3. Repeat badge
        self.repeat_lbl = ctk.CTkLabel(
            self,
            text=self.job.get("repeat_type", "One Time"),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_CLR_SUBTEXT,
            width=70,
            anchor="center"
        )
        self.repeat_lbl.pack(side="left", padx=10)
        self.repeat_lbl.bind("<Button-1>", lambda e: self._row_clicked())

        # 4. Mode / Quality
        self.mode_lbl = ctk.CTkLabel(
            self,
            text=self.job.get("quality", "Video (1080p)"),
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_CLR_SUBTEXT,
            width=100,
            anchor="center"
        )
        self.mode_lbl.pack(side="left", padx=10)
        self.mode_lbl.bind("<Button-1>", lambda e: self._row_clicked())

        # 5. Next Run
        next_run = self.job.get("next_run", "")
        if not next_run:
            next_run = "—"
        self.next_run_lbl = ctk.CTkLabel(
            self,
            text=next_run,
            font=ctk.CTkFont(family="Segoe UI", size=11),
            text_color=_CLR_SUBTEXT,
            width=130,
            anchor="w"
        )
        self.next_run_lbl.pack(side="left", padx=10)
        self.next_run_lbl.bind("<Button-1>", lambda e: self._row_clicked())

    def _row_clicked(self) -> None:
        self.on_click(self.job["uuid"])

    def set_selection(self, selected: bool) -> None:
        self.selected = selected
        self.configure(fg_color="#20232f" if selected else "#1a1d27")
        self.configure(border_color=_CLR_ACCENT if selected else "#2e3347")


class SchedulerPage(BasePage):
    def __init__(self, master: ctk.CTk, manager: any, logger: any) -> None:
        super().__init__(master, manager, logger)
        self.scheduler = None
        self._selected_uuid: str | None = None
        self._row_widgets: dict[str, SchedulerRow] = {}
        
        self._build_ui()

    def _build_ui(self) -> None:
        # Title Header
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(20, 10))
        
        ctk.CTkLabel(
            header,
            text="Download Scheduler",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=_CLR_TEXT,
        ).pack(side="left", anchor="w")
        
        # Subtitle
        ctk.CTkLabel(
            self,
            text="Schedule and automate recurring video/audio downloads.",
            font=ctk.CTkFont(family="Segoe UI", size=12),
            text_color=_CLR_SUBTEXT,
        ).pack(anchor="w", padx=20, pady=(0, 16))

        # Toolbar Frame
        toolbar = ctk.CTkFrame(self, fg_color="transparent")
        toolbar.pack(fill="x", padx=20, pady=(0, 12))

        self.btn_new = ctk.CTkButton(
            toolbar, text="+ New Schedule", width=120, height=30,
            fg_color=_CLR_ACCENT, hover_color=_CLR_ACCENT_HOV, text_color="#ffffff",
            command=self._new_schedule_click, corner_radius=6
        )
        self.btn_new.pack(side="left", padx=(0, 6))

        self.btn_edit = ctk.CTkButton(
            toolbar, text="Edit", width=60, height=30,
            fg_color=_CLR_CARD, hover_color=_CLR_BORDER, text_color=_CLR_TEXT,
            command=self._edit_schedule_click, corner_radius=6, state="disabled"
        )
        self.btn_edit.pack(side="left", padx=4)

        self.btn_delete = ctk.CTkButton(
            toolbar, text="Delete", width=70, height=30,
            fg_color=_CLR_CARD, hover_color=_CLR_BORDER, text_color=_CLR_TEXT,
            command=self._delete_schedule_click, corner_radius=6, state="disabled"
        )
        self.btn_delete.pack(side="left", padx=4)

        self.btn_dup = ctk.CTkButton(
            toolbar, text="Duplicate", width=80, height=30,
            fg_color=_CLR_CARD, hover_color=_CLR_BORDER, text_color=_CLR_TEXT,
            command=self._duplicate_schedule_click, corner_radius=6, state="disabled"
        )
        self.btn_dup.pack(side="left", padx=4)

        self.btn_toggle = ctk.CTkButton(
            toolbar, text="Enable / Disable", width=120, height=30,
            fg_color=_CLR_CARD, hover_color=_CLR_BORDER, text_color=_CLR_TEXT,
            command=self._toggle_schedule_click, corner_radius=6, state="disabled"
        )
        self.btn_toggle.pack(side="left", padx=4)

        self.btn_run_now = ctk.CTkButton(
            toolbar, text="Run Now", width=80, height=30,
            fg_color=_CLR_CARD, hover_color=_CLR_BORDER, text_color=_CLR_TEXT,
            command=self._run_now_click, corner_radius=6, state="disabled"
        )
        self.btn_run_now.pack(side="left", padx=4)

        # Header Row Columns Titles
        self.table_header = ctk.CTkFrame(self, fg_color="transparent", height=20)
        self.table_header.pack(fill="x", padx=20, pady=(4, 4))
        self.table_header.pack_propagate(False)

        ctk.CTkLabel(self.table_header, text="STATUS", font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"), text_color=_CLR_SUBTEXT, width=105, anchor="center").pack(side="left")
        ctk.CTkLabel(self.table_header, text="URL / MEDIA SOURCE", font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"), text_color=_CLR_SUBTEXT, anchor="w").pack(side="left", fill="x", expand=True, padx=10)
        ctk.CTkLabel(self.table_header, text="REPEAT", font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"), text_color=_CLR_SUBTEXT, width=70, anchor="center").pack(side="left", padx=10)
        ctk.CTkLabel(self.table_header, text="QUALITY", font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"), text_color=_CLR_SUBTEXT, width=100, anchor="center").pack(side="left", padx=10)
        ctk.CTkLabel(self.table_header, text="NEXT EXECUTION RUN", font=ctk.CTkFont(family="Segoe UI", size=10, weight="bold"), text_color=_CLR_SUBTEXT, width=130, anchor="w").pack(side="left", padx=10)

        # Scrollable schedules list container
        self.list_container = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            border_color=_CLR_BORDER,
            border_width=0
        )
        self.list_container.pack(fill="both", expand=True, padx=20, pady=(0, 20))

    def on_show(self) -> None:
        if not self.scheduler:
            # Wire scheduler link
            main_window = self.master.master
            self.scheduler = getattr(main_window, "scheduler", None)
            if self.scheduler:
                self.scheduler.register_listener(self._on_scheduler_event)
        self._refresh_list()

    def on_hide(self) -> None:
        pass

    def refresh(self, data: dict[str, Any]) -> None:
        """Called automatically on unified poll tick (Tkinter thread)."""
        self._refresh_list()

    def _refresh_list(self) -> None:
        if not self.scheduler:
            return
            
        jobs = self.scheduler.get_schedules()
        
        # Destroy widgets no longer in db
        job_uuids = {j["uuid"] for j in jobs}
        for uid in list(self._row_widgets.keys()):
            if uid not in job_uuids:
                self._row_widgets[uid].destroy()
                self._row_widgets.pop(uid)
                
        # Draw or update widgets
        for job in jobs:
            uid = job["uuid"]
            if uid in self._row_widgets:
                # Update job model
                row = self._row_widgets[uid]
                row.job = job
                
                # Dynamic labels updates
                row.state_lbl.configure(
                    text=job.get("state", "Scheduled").upper(),
                    fg_color=STATE_COLORS.get(job.get("state", "Scheduled"), "#8b92a8")
                )
                row.url_lbl.configure(text=job.get("url", ""))
                row.repeat_lbl.configure(text=job.get("repeat_type", "One Time"))
                row.mode_lbl.configure(text=job.get("quality", "Video (1080p)"))
                next_run = job.get("next_run", "") or "—"
                row.next_run_lbl.configure(text=next_run)
                row.enabled_lbl.configure(
                    text="●" if job.get("enabled", True) else "○",
                    text_color=_CLR_GREEN if job.get("enabled", True) else _CLR_SUBTEXT
                )
            else:
                row = SchedulerRow(self.list_container, job, self._on_row_click, self._on_row_double_click)
                row.pack(fill="x", pady=4)
                self._row_widgets[uid] = row

        # Restore selection highlighting
        if self._selected_uuid and self._selected_uuid not in self._row_widgets:
            self._selected_uuid = None
            
        for uid, row in self._row_widgets.items():
            row.set_selection(uid == self._selected_uuid)
            
        self._update_toolbar_states()

    def _on_row_click(self, uuid_str: str) -> None:
        if self._selected_uuid == uuid_str:
            self._selected_uuid = None
        else:
            self._selected_uuid = uuid_str
            
        for uid, row in self._row_widgets.items():
            row.set_selection(uid == self._selected_uuid)
            
        self._update_toolbar_states()

    def _on_row_double_click(self, uuid_str: str) -> None:
        self._selected_uuid = uuid_str
        self._edit_schedule_click()

    def _update_toolbar_states(self) -> None:
        sel = self._selected_uuid is not None
        state = "normal" if sel else "disabled"
        self.btn_edit.configure(state=state)
        self.btn_delete.configure(state=state)
        self.btn_dup.configure(state=state)
        self.btn_toggle.configure(state=state)
        self.btn_run_now.configure(state=state)

    def _on_scheduler_event(self, name: str, payload: dict) -> None:
        # Forward updates thread-safely
        if payload and "uuid" in payload:
            self.after(0, self._update_single_job_widget, payload)
        else:
            self.after(0, self._refresh_list)

    def _update_single_job_widget(self, job_data: dict) -> None:
        uid = job_data.get("uuid")
        if not uid:
            return
            
        if uid in self._row_widgets:
            row = self._row_widgets[uid]
            row.job.update(job_data)
            
            # Refresh only this row's widgets in-place
            state = row.job.get("state", "Scheduled")
            row.state_lbl.configure(
                text=state.upper(),
                fg_color=STATE_COLORS.get(state, "#8b92a8")
            )
            row.url_lbl.configure(text=row.job.get("url", ""))
            row.repeat_lbl.configure(text=row.job.get("repeat_type", "One Time"))
            row.mode_lbl.configure(text=row.job.get("quality", "Video (1080p)"))
            next_run = row.job.get("next_run", "") or "—"
            row.next_run_lbl.configure(text=next_run)
            enabled = row.job.get("enabled", True)
            row.enabled_lbl.configure(
                text="●" if enabled else "○",
                text_color=_CLR_GREEN if enabled else _CLR_SUBTEXT
            )
        else:
            # If the job isn't in rows yet (e.g. Schedule Added), trigger a full list refresh
            self._refresh_list()

    # ------------------------------------------------------------------
    # Actions Commands
    # ------------------------------------------------------------------
    def _new_schedule_click(self) -> None:
        ScheduleDialog(self, on_save=self._on_add_save)

    def _on_add_save(self, payload: dict) -> None:
        if self.scheduler:
            self.scheduler.add_schedule(payload)
            self._refresh_list()

    def _edit_schedule_click(self) -> None:
        if not self._selected_uuid or not self.scheduler:
            return
        job = next((j for j in self.scheduler.get_schedules() if j["uuid"] == self._selected_uuid), None)
        if job:
            ScheduleDialog(self, schedule_data=job, on_save=self._on_edit_save)

    def _on_edit_save(self, payload: dict) -> None:
        if self.scheduler and self._selected_uuid:
            self.scheduler.edit_schedule(self._selected_uuid, payload)
            self._refresh_list()

    def _delete_schedule_click(self) -> None:
        if not self._selected_uuid or not self.scheduler:
            return
        ans = messagebox.askyesno("Delete Schedule", "Are you sure you want to delete the selected schedule?", parent=self)
        if ans:
            self.scheduler.delete_schedule(self._selected_uuid)
            self._selected_uuid = None
            self._refresh_list()

    def _duplicate_schedule_click(self) -> None:
        if not self._selected_uuid or not self.scheduler:
            return
        try:
            new_uid = self.scheduler.duplicate_schedule(self._selected_uuid)
            self._selected_uuid = new_uid
            self._refresh_list()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to duplicate schedule: {e}", parent=self)

    def _toggle_schedule_click(self) -> None:
        if not self._selected_uuid or not self.scheduler:
            return
        self.scheduler.toggle_schedule(self._selected_uuid)
        self._refresh_list()

    def _run_now_click(self) -> None:
        if not self._selected_uuid or not self.scheduler:
            return
        self.scheduler.run_now(self._selected_uuid)
        messagebox.showinfo("Scheduler", "Schedule manual execution run triggered.", parent=self)
