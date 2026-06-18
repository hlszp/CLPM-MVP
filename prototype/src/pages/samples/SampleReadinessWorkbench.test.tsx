import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { renderWithSession } from '../../test/renderWithSession';
import { SampleReadinessWorkbench } from './SampleReadinessWorkbench';

describe('SampleReadinessWorkbench freeze flow', () => {
  it('shows validation summary and next-step guidance before freezing', () => {
    renderWithSession(<SampleReadinessWorkbench />);

    expect(screen.getByRole('heading', { name: '质量规则' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '下一步' })).toBeInTheDocument();
    expect(screen.getByText(/状态由 session 驱动/i)).toBeInTheDocument();
    expect(screen.getByText(/GOOD 进入评价/i)).toBeInTheDocument();
    expect(screen.getByText(/冻结前请确认字段缺口和现场核实项已显性留痕/i)).toBeInTheDocument();
  });

  it('freezes sample and shows read-only state', async () => {
    const user = userEvent.setup();

    renderWithSession(<SampleReadinessWorkbench />);

    await user.click(screen.getByRole('button', { name: /冻结样本/i }));

    expect(screen.getByText(/当前状态：frozen/i)).toBeInTheDocument();
    expect(screen.getByText(/样本已冻结，字段映射只读/i)).toBeInTheDocument();
  });
});
