import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import HumanInterventions from "./HumanInterventions";
import { confirmIntervention, getInterventions } from "../lib/api";

vi.mock("../lib/api", () => ({ getInterventions: vi.fn(), confirmIntervention: vi.fn() }));

describe("HumanInterventions", () => {
  beforeEach(() => vi.resetAllMocks());

  it("acknowledges the held attempt without calling a submit API", async () => {
    vi.mocked(getInterventions).mockResolvedValue([
      { id: "same-attempt", job_url: "https://jobs.lever.co/acme/one", message: "CAPTCHA", acknowledged: 0 },
    ]);
    vi.mocked(confirmIntervention).mockResolvedValue({ success: true });
    render(<HumanInterventions />);
    const button = await screen.findByRole("button", { name: "Resolvi o desafio" });
    expect(screen.getByText(/sem clicar novamente em enviar/)).toBeInTheDocument();
    await userEvent.click(button);
    await waitFor(() => expect(confirmIntervention).toHaveBeenCalledExactlyOnceWith("same-attempt"));
  });

  it("does not offer confirmation when the acknowledgement is already pending", async () => {
    vi.mocked(getInterventions).mockResolvedValue([
      { id: "same-attempt", job_url: "https://jobs.lever.co/acme/one", message: "CAPTCHA", acknowledged: 1 },
    ]);
    render(<HumanInterventions />);
    expect(await screen.findByRole("button", { name: /Conferindo/ })).toBeDisabled();
    expect(confirmIntervention).not.toHaveBeenCalled();
  });
});
