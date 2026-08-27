/**
 * 处置 Tab 共享派生类型
 *
 * staff_load 由在办 orders（pending+executing+verifying）按 handler 聚合派生
 * （handling.ts 无 staff-load 专用端点，A-08 后端未落地）
 */

/** 人员负载行（前端从在办 orders 按 handler 聚合派生） */
export interface StaffLoadItem {
  handler: string;
  pending: number;
  executing: number;
  verifying: number;
  overdue: number;
}
