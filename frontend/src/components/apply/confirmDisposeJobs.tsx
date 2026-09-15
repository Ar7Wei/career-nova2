import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import { tNow } from '@/lib/i18n'
import { userErrorText } from '@/lib/errors'

/**
 * 岗位处置确认（2026-08-25 方向口头化）：commit 方向后，后端返回待处置的未处理岗位数，
 * 前端就地弹「留/删」确认——留 = 什么都不做；删 = 调 onClear 清未处理岗位。
 *
 * 两处共用（简历页 agent commit 后 / 投递面板保存后）：方向决策由 agent/面板敲定，
 * 岗位处置由后端卡门 + 前端弹窗，与 agent 无关（apply.md §11.2ter 修订）。
 *
 * @param count 待处置的未处理岗位数（>0 才调用）。
 * @param onClear 用户选「清」时的回调（内部已捕获失败出声）。
 */
export function confirmDisposeJobs(count: number, onClear: () => Promise<unknown>): void {
  modals.openConfirmModal({
    title: tNow('apply.directionDisposeTitle'),
    children: (
      <div className="direction-confirm">
        <p>{tNow('apply.directionDisposeHint').replace('{n}', String(count))}</p>
      </div>
    ),
    labels: {
      confirm: tNow('apply.directionDeleteOld'),
      cancel: tNow('apply.directionKeepOld'),
    },
    confirmProps: { color: 'red' },
    cancelProps: { color: 'green', variant: 'light' },
    onConfirm: async () => {
      try {
        await onClear()
      } catch (err) {
        notifications.show({ color: 'red', title: tNow('apply.consoleCriteria'), message: userErrorText(err) })
      }
    },
  })
}
