import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button } from '@mantine/core'
import { RotateCcw } from 'lucide-react'

interface PanelErrorBoundaryProps {
  /** 面板名（出错提示里显示，如「资料集」）。 */
  name: string
  children: ReactNode
}

interface PanelErrorBoundaryState {
  error: Error | null
}

/**
 * 局部渲染错误兜底（2026-09-09 资料集编辑白屏后补）：项目无全局 ErrorBoundary，
 * 任何渲染期抛错都会整树卸载 = 白屏。这里把单个面板（资料集/优化点/编辑项）包起来，
 * 崩了只降级这一块——显示「出错 + 重试」，不拖垮整页；错误详情打到 console 供排查。
 *
 * 类组件：ErrorBoundary 只能用 class（getDerivedStateFromError / componentDidCatch），
 * 这是项目里唯一的 class 组件，合理例外。
 */
export class PanelErrorBoundary extends Component<PanelErrorBoundaryProps, PanelErrorBoundaryState> {
  state: PanelErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): PanelErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 出声到 console（含组件栈），便于复现排查；不上报（本地单用户）。
    console.error(`[PanelErrorBoundary] ${this.props.name} 渲染崩溃`, error, info.componentStack)
  }

  /** 重试：清掉错误重渲染子树（若是瞬时态/脏数据导致，重挂能恢复）。 */
  private reset = () => this.setState({ error: null })

  render() {
    if (this.state.error) {
      return (
        <div className="panel-error-boundary">
          <div className="panel-error-boundary-text">{this.props.name} 出错了</div>
          <Button
            variant="default"
            size="compact-sm"
            leftSection={<RotateCcw size={12} />}
            onClick={this.reset}
          >
            重试
          </Button>
        </div>
      )
    }
    return this.props.children
  }
}
