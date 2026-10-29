import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import Pipeline from "./Pipeline";
import { getPipeline } from "../lib/api";
import type { PipelineData, PipelineJob } from "../lib/types";
import "../i18n";

vi.mock("../lib/api", () => ({ getPipeline: vi.fn() }));
const makeJob = (n: number, state: string): PipelineJob => ({
  job_key: `linkedin:${n}`, title: `Engineer ${n}`, company: "Example Labs", board: "", url: `https://linkedin.com/jobs/view/${n}`,
  platform: "linkedin", location: "Remote", collected_at: "2026-01-01", source: "linkedin",
  triage: { state, reason: "fictitious_reason", bucket: "Other", evidence: ["Fictitious evidence"] },
  attempts: [], application_status: "not-attempted", outcome: { stage: "no_reply", reply_at: "", ambiguous: false },
  last_update: "2026-01-01", path: ["collected", `triage:${state}`, ...(state === "eligible" ? ["application:not-attempted"] : [])],
});
const fixture: PipelineData = {
  generated_at: "2026-01-01", totals: { collected: 2, eligible: 1, sent: 0, confirmed: 0, advanced: 0 },
  jobs: [makeJob(1, "eligible"), makeJob(2, "pending")],
  sankey: { nodes: [{ id: "collected", label: "Collected", stage: 0 }, { id: "triage:eligible", label: "Eligible", stage: 1 }, { id: "triage:pending", label: "Pending: Other", stage: 1 }, { id: "application:not-attempted", label: "Not attempted", stage: 2 }], links: [
    { source: "collected", target: "triage:eligible", value: 1 }, { source: "collected", target: "triage:pending", value: 1 }, { source: "triage:eligible", target: "application:not-attempted", value: 1 },
  ] },
};

beforeEach(() => { vi.mocked(getPipeline).mockResolvedValue(structuredClone(fixture)); });

describe("Pipeline", () => {
  it("renders KPI values and SVG Sankey nodes", async () => {
    render(<Pipeline />);
    expect(await screen.findByTestId("kpi-collected")).toHaveTextContent("2");
    expect(screen.getByTestId("kpi-eligible")).toHaveTextContent("1");
    expect(screen.getByTestId("kpi-confirmed")).toHaveTextContent("0");
    expect(screen.getByRole("button", { name: "Collected: 2" }).tagName.toLowerCase()).toBe("g");
    expect(screen.getByText("Engineer 1")).toBeInTheDocument();
    expect(screen.getByText("Engineer 2")).toBeInTheDocument();
  });
  it("filters explorer by node and connection, and resets", async () => {
    render(<Pipeline />);
    fireEvent.click(await screen.findByRole("button", { name: "Eligible: 1" }));
    expect(screen.getByText("Engineer 1")).toBeInTheDocument();
    expect(screen.queryByText("Engineer 2")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Reset filters"));
    fireEvent.click(screen.getByRole("button", { name: "Collected → Pending: Other: 1" }));
    expect(screen.queryByText("Engineer 1")).not.toBeInTheDocument();
    expect(screen.getByText("Engineer 2")).toBeInTheDocument();
  });
  it("supports search, status filters and details", async () => {
    HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
    render(<Pipeline />);
    await screen.findByText("Engineer 1");
    fireEvent.change(screen.getByRole("textbox", { name: "Search jobs, companies or reasons" }), { target: { value: "Engineer 2" } });
    expect(screen.queryByText("Engineer 1")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Engineer 2"));
    const detail = screen.getByLabelText("Engineer 2");
    expect(within(detail).getByText("fictitious_reason")).toBeInTheDocument();
    expect(within(detail).getByText("Fictitious evidence")).toBeInTheDocument();
    expect(within(detail).getByRole("link", { name: "Open job posting" })).toHaveAttribute("href", fixture.jobs[1].url);
  });
  it("paginates large collections", async () => {
    vi.mocked(getPipeline).mockResolvedValue({ ...fixture, jobs: Array.from({ length: 120 }, (_, i) => makeJob(i, "eligible")) });
    render(<Pipeline />);
    await screen.findByTestId("kpi-collected");
    expect(screen.getAllByRole("row")).toHaveLength(51);
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Page 2 of 3")).toBeInTheDocument();
  });
  it("shows an error on failure and handles empty snapshots", async () => {
    vi.mocked(getPipeline).mockRejectedValueOnce(new Error("offline"));
    const rendered = render(<Pipeline />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    rendered.unmount();
    vi.mocked(getPipeline).mockResolvedValue({ ...fixture, jobs: [], sankey: { nodes: [], links: [] } });
    render(<Pipeline />);
    expect(await screen.findByText("No jobs collected yet.")).toBeInTheDocument();
  });
});
