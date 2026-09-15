import { useEffect, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, Save } from 'lucide-react'
import { Button, Switch, NumberInput } from '@mantine/core'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import { useDirectionStore } from '@/stores/directionStore'
import { useApplyStore } from '@/stores/applyStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useT } from '@/lib/i18n'
import { userErrorText } from '@/lib/errors'
import { QueryGroupsEditor } from './QueryGroupsEditor'
import { PlatformLoginBar } from './PlatformLoginBar'
import { confirmDisposeJobs } from './confirmDisposeJobs'

/**
 * 检索控制台浮窗内容（投递页）：三个平行模块，2026-09-01 整合定稿——
 * ① 检索条件（可折叠）→ ② 岗位获取（可折叠，开关常驻题目行、数字折进）→ ③ 登录平台（恒展开）。
 *
 * 由 ConsoleTrigger 包在 <ToolTab> 浮窗里打开（与简历页优化点/资料集同一套工具坞语言）。
 * 打开时读当前方向回填草稿；两块折叠态存 applyStore（单次启动内记住）。
 *
 * 2026-08-20 定稿：方向主要由**聊天 agent 自动生成**（简历页聊出目标 → refine_direction/
 * commit_direction 工具 → 写回 facts）。本浮窗只读当前方向 + 手填编辑 + 保存（commit）。
 * 2026-09-01：「求职之路」开关 + 详细设置并入本浮窗为「岗位获取」块（原 CrawlIndicator
 * 弹窗内容），并更名「岗位获取」。
 */

/** 折叠块题目行（整行可点）：标题 + Chevron 紧贴左；右侧可选（岗位获取的开关）。
 *  用 Mantine Button（variant="subtle"）而非原生 button——整行左对齐、无底色/无圆角，
 *  右侧的 Switch 挂在 Button 的 rightSection 里（Button 内是 span 不是 button，Switch 合法嵌套）。 */
function SectionHead({
  title,
  open,
  onToggle,
  right,
}: {
  title: string
  open: boolean
  onToggle: () => void
  right?: ReactNode
}) {
  return (
    <Button
      variant="subtle"
      className="console-section-head"
      classNames={{ label: 'console-section-head-label', section: 'console-section-right' }}
      onClick={onToggle}
      rightSection={right}
      leftSection={
        <>
          <span className="console-section-title">{title}</span>
          {open ? <ChevronDown size={14} className="console-section-chevron" /> : <ChevronRight size={14} className="console-section-chevron" />}
        </>
      }
    />
  )
}

/** 折叠块骨架：题目行 + 可折叠体（含块底右对齐 footer）。 */
function Section({
  title,
  section,
  right,
  footer,
  children,
}: {
  title: string
  section: 'criteria' | 'crawl'
  right?: ReactNode
  footer?: ReactNode
  children: ReactNode
}) {
  const open = useApplyStore((s) => s.consoleSections[section])
  const toggle = useApplyStore((s) => s.toggleConsoleSection)
  return (
    <div className="console-section">
      <SectionHead title={title} open={open} onToggle={() => toggle(section)} right={right} />
      {open && (
        <div className="console-section-body">
          {children}
          {footer && <div className="console-section-footer">{footer}</div>}
        </div>
      )}
    </div>
  )
}

export function DirectionPanel() {
  const t = useT()
  const direction = useDirectionStore((s) => s.direction)
  const committing = useDirectionStore((s) => s.committing)
  const cities = useDirectionStore((s) => s.cities)
  const citiesLoading = useDirectionStore((s) => s.citiesLoading)
  const load = useDirectionStore((s) => s.load)
  const loadCities = useDirectionStore((s) => s.loadCities)
  const commit = useDirectionStore((s) => s.commit)
  const disposeUnprocessed = useDirectionStore((s) => s.disposeUnprocessed)

  const applyMode = useSettingsStore((s) => s.apply_mode)
  const crawlQuota = useSettingsStore((s) => s.crawl_quota)
  const crawlCap = useSettingsStore((s) => s.crawl_cap)
  const crawlThreshold = useSettingsStore((s) => s.crawl_threshold)
  const update = useSettingsStore((s) => s.update)
  const unprocessed = useApplyStore((s) => s.counts.unprocessed)
  const kick = useApplyStore((s) => s.kick)

  const [keywords, setKeywords] = useState<string[][]>([[]])
  const [groupCities, setGroupCities] = useState<string[]>([''])
  const [quotaLiepin, setQuotaLiepin] = useState(crawlQuota.liepin)
  const [quotaJob51, setQuotaJob51] = useState(crawlQuota.job51)
  const [cap, setCap] = useState(crawlCap)
  const [threshold, setThreshold] = useState(crawlThreshold)
  const [savingDetail, setSavingDetail] = useState(false)

  // 挂载读当前方向 + 城市库。城市库另经 ToolTab.onOpen 预取、QueryGroupsEditor 挂载兜底拉——
  // 三处都读 getState 无依赖，幂等重入无副作用（SuggestionBasket 同源教训：别把 store 的
  // 动作塞进依赖数组，动作引用虽稳定也别给 effect 埋重跑口子）。
  useEffect(() => {
    void load()
    void loadCities()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 当前方向变化 → 回填草稿（城市与查询组平行等长，ADR 0019）
  useEffect(() => {
    const kws = direction?.keywords ?? [[]]
    setKeywords(kws)
    setGroupCities(Array.from({ length: kws.length }, (_, i) => direction?.cities[i] ?? ''))
  }, [direction])

  /** 清洗关键词（去空白词条、去空组）。 */
  const clean = (groups: string[][]) =>
    groups.map((g) => g.map((w) => w.trim()).filter(Boolean)).filter((g) => g.length > 0)

  /** 只保留非空组，并与城市平行对齐（清洗后仍等长，城市随组走）。 */
  const nonEmpty = keywords
    .map((g, i) => ({ words: g.map((w) => w.trim()).filter(Boolean), city: (groupCities[i] ?? '').trim() }))
    .filter((g) => g.words.length > 0)
  const cleanedKeywords = nonEmpty.map((g) => g.words)
  const cleanedCities = nonEmpty.map((g) => g.city)

  /** 是否相对已存方向有改动（决定保存按钮可用性）。 */
  const isDirty =
    JSON.stringify(cleanedKeywords) !== JSON.stringify(clean(direction?.keywords ?? [[]])) ||
    JSON.stringify(cleanedCities) !== JSON.stringify(clean(direction?.keywords ?? [[]]).map((_, i) => (direction?.cities[i] ?? '').trim()))

  /** 保存按钮可用：有改动 + 关键词非空 + **每组都有城市**（逐组必填，ADR 0019）+ 未在提交中。 */
  const allCitiesFilled = cleanedCities.length > 0 && cleanedCities.every((c) => c !== '')
  const canSave = isDirty && cleanedKeywords.length > 0 && allCitiesFilled && !committing

  /** 岗位获取详细设置是否相对当前设置有改动（决定保存按钮可用性）。 */
  const detailDirty =
    quotaLiepin !== crawlQuota.liepin ||
    quotaJob51 !== crawlQuota.job51 ||
    cap !== crawlCap ||
    threshold !== crawlThreshold

  /** 保存：role 沿用已存方向（面板不再编辑 role）；commit 只写方向，返回待处置岗位数，
   *  >0 就地弹「留/删」确认（留 = 什么都不做；删 = disposeUnprocessed 清未处理岗位）。 */
  const handleSave = async () => {
    if (!canSave) return
    try {
      const pending = await commit(direction?.role ?? '', cleanedKeywords, cleanedCities)
      if (pending > 0) {
        confirmDisposeJobs(pending, async () => {
          await disposeUnprocessed()
        })
      }
      notifications.show({ color: 'green', title: t('apply.consoleCriteria'), message: t('apply.directionSaved') })
    } catch (err) {
      notifications.show({ color: 'red', title: t('apply.consoleCriteria'), message: userErrorText(err) })
    }
  }

  /** 保存岗位获取详细设置（quota/cap/触发值）。与检索条件对齐：dirty 门控 + 转圈 loading。 */
  const saveDetail = async () => {
    if (savingDetail) return
    setSavingDetail(true)
    try {
      await update({
        crawl_quota: { liepin: quotaLiepin, job51: quotaJob51 },
        crawl_cap: cap,
        crawl_threshold: threshold,
      })
      notifications.show({ color: 'green', title: t('apply.crawlTitle'), message: t('apply.modeDetailSaved') })
    } catch (err) {
      notifications.show({ color: 'red', title: t('apply.crawlTitle'), message: userErrorText(err) })
    } finally {
      setSavingDetail(false)
    }
  }

  /** 切换开关：关 = 直接关；开 = 先确认方向（空则引导，非空则弹确认）。 */
  const handleToggle = (next: boolean) => {
    if (!next) {
      void update({ apply_mode: false }).catch((err) => {
        notifications.show({ color: 'red', title: t('apply.crawlTitle'), message: userErrorText(err) })
      })
      return
    }
    // 开启：方向硬门控
    if (!direction || direction.keywords.length === 0) {
      notifications.show({ color: 'yellow', title: t('apply.crawlTitle'), message: t('apply.modeNeedDirection') })
      return
    }
    const role = direction.role
    const cityDesc = Array.from(new Set(direction.cities.filter(Boolean))).join('、') || t('apply.modeCityAny')
    modals.openConfirmModal({
      title: t('apply.crawlTitle'),
      children: (
        <div className="direction-confirm">
          <p>{t('apply.modeConfirm').replace('{role}', role).replace('{city}', cityDesc)}</p>
        </div>
      ),
      labels: { confirm: t('apply.modeConfirmYes'), cancel: t('common.cancel') },
      confirmProps: { color: 'green' },
      onConfirm: () => {
        void update({ apply_mode: true })
          .then(() => {
            // 开开关 = 触发点④（apply.md §8①）：apply_mode 关→开，踢一次。
            void kick()
          })
          .catch((err) => {
            notifications.show({ color: 'red', title: t('apply.crawlTitle'), message: userErrorText(err) })
          })
      },
    })
  }

  return (
    <div className="console-modal-body">
      {/* 浮窗标题（原 Modal 标题移入：ToolTab 浮窗无自带标题栏） */}
      <div className="console-panel-title">{t('apply.consoleTitle')}</div>

      {/* 模块一：检索条件（可折叠，默认收起） */}
      <Section
        title={t('apply.consoleCriteria')}
        section="criteria"
        footer={
          <Button
            color="green"
            size="compact-sm"
            leftSection={<Save size={14} />}
            loading={committing}
            disabled={!canSave}
            onClick={() => void handleSave()}
          >
            {committing ? t('apply.directionSaving') : t('apply.directionSave')}
          </Button>
        }
      >
        <QueryGroupsEditor
          value={keywords}
          cities={groupCities}
          onValueChange={setKeywords}
          onCitiesChange={setGroupCities}
          cityOptions={cities}
          citiesLoading={citiesLoading}
          disabled={committing}
          label={t('apply.directionKeywords')}
          description={t('apply.directionKeywordsDesc')}
        />
      </Section>

      {/* 模块二：岗位获取（可折叠，默认收起；开关常驻题目行靠右，数字折进） */}
      <Section
        title={t('apply.crawlTitle')}
        section="crawl"
        right={
          <Switch
            checked={applyMode}
            onChange={(e) => handleToggle(e.currentTarget.checked)}
            size="md"
            color="green"
            onLabel={t('apply.modeOn')}
            offLabel={t('apply.modeOff')}
          />
        }
        footer={
          <Button
            color="green"
            size="compact-sm"
            leftSection={<Save size={14} />}
            loading={savingDetail}
            disabled={!detailDirty}
            onClick={() => void saveDetail()}
          >
            {t('apply.directionSave')}
          </Button>
        }
      >
        <span className="apply-mode-status">{t('apply.modeDesc')}</span>
        {applyMode && (
          <span className="apply-mode-status">{t('apply.modeBacklog').replace('{n}', String(unprocessed))}</span>
        )}

        <div className="crawl-modal-section">
          <div className="crawl-modal-section-title">{t('apply.modeQuotaTitle')}</div>
          <div className="crawl-modal-grid">
            <NumberInput
              size="xs"
              value={quotaLiepin}
              onChange={(v) => setQuotaLiepin(Number(v) || 0)}
              min={1}
              label={t('apply.modeQuotaLiepin')}
              className="apply-mode-number"
            />
            <NumberInput
              size="xs"
              value={quotaJob51}
              onChange={(v) => setQuotaJob51(Number(v) || 0)}
              min={1}
              label={t('apply.modeQuotaJob51')}
              className="apply-mode-number"
            />
          </div>
        </div>

        <div className="crawl-modal-section">
          <NumberInput
            size="xs"
            value={cap}
            onChange={(v) => setCap(Number(v) || 0)}
            min={1}
            label={t('apply.modeCap')}
            description={t('apply.modeCapDesc')}
            className="apply-mode-number"
          />
          <NumberInput
            size="xs"
            value={threshold}
            onChange={(v) => setThreshold(Number(v) || 0)}
            min={1}
            label={t('apply.modeThreshold')}
            description={t('apply.modeThresholdDesc')}
            className="apply-mode-number"
          />
        </div>
      </Section>

      {/* 模块三：登录平台（恒展开，无 icon） */}
      <PlatformLoginBar />
    </div>
  )
}
