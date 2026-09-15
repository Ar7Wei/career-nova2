import { useT } from '@/lib/i18n'
import { Check, X, MessageCircle, Download, Sparkles, FolderOpen } from 'lucide-react'
import { ApiTester } from './ApiTester'
import { Button, TextInput, Textarea, Select, Tree, Modal, Group, Tooltip, type TreeNodeData } from '@mantine/core'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import { useState } from 'react'

/**
 * 组件试衣间（隐藏路由 /dev/components，不进菜单）。
 * 摆出全部按 Alger 重写的原语，供调视觉与回归验收；附接口调用器。
 * 2026-08：接入 Mantine v9，新增 Mantine 组件 section 验证 Alger 主题映射。
 */

const demoTree: TreeNodeData[] = [
  {
    value: 'a',
    label: '某金融科技公司 前端工程师 2020-至今',
    children: [
      { value: 'a1', label: '主导核心交易系统前端重构' },
      { value: 'a2', label: '组件复用率提升40%' },
    ],
  },
  {
    value: 'b',
    label: '另一家公司 后端工程师 2018-2020',
    children: [{ value: 'b1', label: '订单系统开发' }],
  },
]
export function DevComponentsPage() {
  const t = useT()
  const [demoModalOpen, setDemoModalOpen] = useState(false)

  return (
    <div>
      <div className="page-header">
        <h1 className="page-title">{t('dev.components')}</h1>
        <p className="page-subtitle">Alger 原语一览 + 接口调用器（仅开发用）</p>
      </div>

      <div className="dev-section">
        <h3>接口调用器（始终打真接口）</h3>
        <ApiTester />
      </div>

      <div className="dev-section">
        <h3>按钮规范定稿（2026-08-24）</h3>
        <p style={{ color: 'var(--color-muted)', fontSize: 'var(--font-size-sm)', marginBottom: 'var(--space-2)' }}>
          已废除 subtle（幽灵钮）。现用三种形态：filled 主操作 / default 白底描边 / light 浅底；危险红保留 subtle 红。
        </p>
        <div className="card">
          {/* 主操作 */}
          <div className="dev-matrix-row">
            <span className="dev-matrix-label">主操作</span>
            <Button size="sm">filled</Button>
            <Button size="sm" variant="light">light</Button>
            <Button size="sm" variant="default">default</Button>
          </div>
          {/* 工具条入口（优化点/资料集 语境） */}
          <div className="dev-matrix-row">
            <span className="dev-matrix-label">工具条入口</span>
            <Button size="sm" variant="default" leftSection={<Sparkles size={13} />}>优化点 3</Button>
            <Button size="sm" variant="light" leftSection={<FolderOpen size={13} />}>资料集</Button>
          </div>
          {/* 面板内彩色动作（接受/拒绝/讨论 语境） */}
          <div className="dev-matrix-row">
            <span className="dev-matrix-label">面板动作·light</span>
            <Button size="compact-sm" variant="light" color="green" leftSection={<Check size={13} />}>接受</Button>
            <Button size="compact-sm" variant="light" color="gray" leftSection={<X size={13} />}>拒绝</Button>
            <Button size="compact-sm" variant="light" color="blue" leftSection={<MessageCircle size={13} />}>讨论</Button>
          </div>
          <div className="dev-matrix-row">
            <span className="dev-matrix-label">面板动作·仅图标</span>
            <Button className="btn-icon btn-icon-sm" size="compact-sm" variant="light" color="green" aria-label="接受"><Check size={13} /></Button>
            <Button className="btn-icon btn-icon-sm" size="compact-sm" variant="light" color="gray" aria-label="拒绝"><X size={13} /></Button>
            <Button className="btn-icon btn-icon-sm" size="compact-sm" variant="light" color="blue" aria-label="讨论"><MessageCircle size={13} /></Button>
          </div>
          {/* 左栏顶条动作（第N稿/下载/移除 语境） */}
          <div className="dev-matrix-row">
            <span className="dev-matrix-label">顶条动作</span>
            <Button size="sm" variant="default">第3稿</Button>
            <Button size="sm" variant="default" leftSection={<Download size={13} />}>下载</Button>
            <Button className="btn-icon" size="sm" variant="default" aria-label="移除"><X size={14} /></Button>
          </div>
          {/* 危险红钮（保留 subtle 红） */}
          <div className="dev-matrix-row">
            <span className="dev-matrix-label">危险红</span>
            <Button size="sm" variant="subtle" color="red">清缓存</Button>
            <Button size="sm" variant="subtle" color="red">全部重置</Button>
          </div>
          {/* hover 提示：Mantine Tooltip */}
          <div className="dev-matrix-row">
            <span className="dev-matrix-label">hover 提示</span>
            <Tooltip label="接受这条建议" withArrow>
              <Button className="btn-icon btn-icon-sm" size="compact-sm" variant="light" color="green" aria-label="接受"><Check size={13} /></Button>
            </Tooltip>
          </div>
        </div>
      </div>

      <div className="dev-section">
        <h3>徽标 Chips</h3>
        <div className="card">
          <div className="dev-row">
            <span className="chip chip-green">已连接</span>
            <span className="chip chip-red">失败</span>
            <span className="chip chip-blue">信息</span>
            <span className="chip chip-gray">默认</span>
          </div>
        </div>
      </div>

      <div className="dev-section">
        <h3>表单 Forms</h3>
        <div className="card">
          <div className="dev-row" style={{ alignItems: 'flex-start' }}>
            <TextInput label="文本输入" placeholder="占位文字…" style={{ width: 220 }} />
            <Select
              label="下拉选择"
              placeholder="选一个"
              data={['选项一', '选项二', '选项三']}
              style={{ width: 220 }}
            />
            <Textarea label="多行文本" placeholder="多行…" style={{ width: 260 }} minRows={3} />
          </div>
        </div>
      </div>

      <div className="dev-section">
        <h3>表格 Table</h3>
        <div className="card">
          <table>
            <thead>
              <tr><th>公司</th><th>岗位</th><th>状态</th></tr>
            </thead>
            <tbody>
              <tr><td>示例科技</td><td>前端工程师</td><td><span className="chip chip-green">已投递</span></td></tr>
              <tr><td>样例互联</td><td>全栈工程师</td><td><span className="chip chip-blue">笔试</span></td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="dev-section">
        <h3>状态点 Status dots</h3>
        <div className="card">
          <div className="dev-row">
            <span className="status-dot green" /> <span className="status-dot gray" /> <span className="status-dot red" />
          </div>
        </div>
      </div>

      <div className="dev-section">
        <h3>空态 Empty state</h3>
        <div className="card">
          <div className="empty-state">
            <h3>暂无数据</h3>
            <p>这里还没有内容，稍后再来看看。</p>
          </div>
        </div>
      </div>

      <div className="dev-section">
        <h3>玻璃拟态 Glass</h3>
        <div className="glass" style={{ padding: '1.25rem' }}>
          <strong>玻璃拟态弹层</strong>
          <p style={{ color: 'var(--color-muted)', fontSize: 13.5, marginTop: 4 }}>
            backdrop-filter: blur(16px) saturate(1.8)，用于 popover / tooltip / dialog。
          </p>
        </div>
      </div>

      <div className="dev-section">
        <h3>Mantine 组件（v9，Alger 主题映射验证）</h3>
        <div className="card">
          <div className="dev-row">
            <Button>主按钮</Button>
            <Button variant="light">浅色</Button>
            <Button variant="outline">描边</Button>
            <Button variant="default">白底</Button>
            <Button size="compact-sm">小号</Button>
          </div>
          <div className="dev-row" style={{ marginTop: '0.75rem' }}>
            <TextInput placeholder="文本输入…" label="文本输入" style={{ width: 240 }} />
            <Select
              label="下拉选择"
              placeholder="选一个"
              data={['选项一', '选项二', '选项三']}
              defaultValue="选项一"
              style={{ width: 240 }}
            />
          </div>
          <div className="dev-row" style={{ marginTop: '0.75rem', alignItems: 'flex-start' }}>
            <div style={{ width: 320 }}>
              <label style={{ fontSize: 13, fontWeight: 600 }}>Tree 树形（抽取卡片用）</label>
              <Tree data={demoTree} />
            </div>
          </div>
          <div className="dev-row" style={{ marginTop: '0.75rem', alignItems: 'center' }}>
            <Button size="compact-sm" variant="default" onClick={() => setDemoModalOpen(true)}>
              Modal 弹窗
            </Button>
            <Button size="compact-sm" variant="default" onClick={() => setDemoModalOpen(false)}>
              （关闭）
            </Button>
            <Button
              size="compact-sm"
              variant="default"
              onClick={() =>
                modals.openConfirmModal({
                  title: '确认弹窗',
                  centered: true,
                  children: <p>确认 Modal（modals.openConfirmModal）</p>,
                  labels: { confirm: '确定', cancel: '取消' },
                  confirmProps: { color: 'green' },
                })
              }
            >
              Confirm 确认弹窗
            </Button>
            <Button
              size="compact-sm"
              variant="default"
              onClick={() =>
                notifications.show({
                  color: 'green',
                  title: '通知',
                  message: 'Notifications toast（Mantine）',
                })
              }
            >
              Toast 通知
            </Button>
          </div>
        </div>
      </div>

      <Modal opened={demoModalOpen} onClose={() => setDemoModalOpen(false)} title="Modal 弹窗" centered>
        <p>声明式 Modal（目标岗位输入等用这个）。</p>
        <TextInput placeholder="输入点什么…" style={{ marginTop: '0.75rem' }} />
        <Group justify="flex-end" mt="md">
          <Button variant="subtle" onClick={() => setDemoModalOpen(false)}>
            取消
          </Button>
          <Button onClick={() => setDemoModalOpen(false)}>确定</Button>
        </Group>
      </Modal>
    </div>
  )
}
