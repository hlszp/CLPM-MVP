import { mount, flushPromises } from '@vue/test-utils';
import { expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getLoopBatchSnapshotsApi: vi.fn(),
  getLoopBatchSnapshotApi: vi.fn(),
  restoreLoopBatchSnapshotApi: vi.fn(),
}));
vi.mock('#/api/engineering-template', () => api);
vi.mock('ant-design-vue', () => ({
  Alert: { props: ['message'], template: '<div>{{message}}</div>' },
  Modal: { props: ['open'], template: '<div v-if="open"><slot/></div>' },
  Button: { props: ['disabled'], template: '<button :disabled="disabled" @click="$emit(\'click\')"><slot/></button>' },
  Input: { props: ['value'], emits: ['update:value'], template: '<input :value="value" @input="$emit(\'update:value\', $event.target.value)" />' },
  Table: { name: 'AuditTable', props: ['rowSelection'], template: '<div/>' },
}));

import BatchRestoreModal from '#/views/config/engineering-templates/batch-restore-modal.vue';

it('changing selected snapshot A to B must never preview stale A without rereading', async () => {
  api.getLoopBatchSnapshotsApi.mockResolvedValue({items: [{snapshotId: 'snapshot-A'}, {snapshotId: 'snapshot-B'}], total: 2});
  api.getLoopBatchSnapshotApi.mockImplementation(async (snapshotId: string) => ({
    snapshotId, total: 1, offset: 0, limit: 200,
    items: [{loopId: `${snapshotId}-loop`, tagName: `${snapshotId}-loop`, before: {}, after: {}}],
  }));
  api.restoreLoopBatchSnapshotApi.mockImplementation(async (snapshotId: string, request: any) => ({
    snapshotId, affected: 1, dryRun: request.dryRun,
    results: request.loopIds.map((loopId: string) => ({loopId, status: 'OK'})),
  }));
  const wrapper = mount(BatchRestoreModal, {props: {open: true, snapshotIds: ['snapshot-A']}});
  await flushPromises();
  expect(wrapper.get('[data-testid="restore-denominator"]').text()).toContain('完整快照 1 条');
  const history = wrapper.findAllComponents({name: 'AuditTable'}).find(table => table.props('rowSelection'))!;
  history.props('rowSelection').onChange(['snapshot-B']);
  await flushPromises();
  await wrapper.get('[data-testid="restore-reason"]').setValue('AUDIT-F04 selection isolation');
  await wrapper.get('[data-testid="btn-preview-restore"]').trigger('click');
  await flushPromises();
  console.log('AUDIT_F04_RESTORE_CALLS', JSON.stringify(api.restoreLoopBatchSnapshotApi.mock.calls));
  expect(api.restoreLoopBatchSnapshotApi).not.toHaveBeenCalledWith('snapshot-A', expect.anything());
  wrapper.unmount();
});
