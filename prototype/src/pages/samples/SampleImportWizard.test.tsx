import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { SampleImportWizard } from './SampleImportWizard';
import { renderWithSession } from '../../test/renderWithSession';

describe('SampleImportWizard state', () => {
  it('updates import method in session when choosing OPC read-only connection', async () => {
    const user = userEvent.setup();

    renderWithSession(<SampleImportWizard />);

    expect(screen.getByRole('heading', { name: '导入方式' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '解析结果' })).toBeInTheDocument();
    expect(screen.getByRole('rowheader', { name: 'pv' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /OPC 只读连接/i }));

    expect(screen.getByText(/当前导入方式：OPC 只读连接/i)).toBeInTheDocument();
    expect(screen.getByText(/control_loop_second_level_24loops_1h\.csv/i)).toBeInTheDocument();
    expect(screen.getByText(/MODE · 工艺确认手动原因与投用定义/i)).toBeInTheDocument();
  });
});
