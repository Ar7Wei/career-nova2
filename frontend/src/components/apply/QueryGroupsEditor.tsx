import { ActionIcon, Button, Input, Select, TagsInput } from '@mantine/core'
import { Plus, X } from 'lucide-react'
import { useEffect } from 'react'
import { useT } from '@/lib/i18n'
import { MAX_KEYWORD_GROUPS } from '@/lib/direction'
import { useDirectionStore } from '@/stores/directionStore'

/**
 * 查询组编辑器（投递·方向）：编辑二维查询组 keywords: string[][] + **逐组城市** cities: string[]。
 *
 * 组内 AND（一行内多个词条 = 一条搜索里需同时满足）、组间 OR（多行 = 多组独立搜索合并去重）。
 * 每组 = 一个 TagsInput（回车加词）+ 一个城市 Select + 行尾删除组按钮；底部「添加条件组」。
 * 组数上限 MAX_KEYWORD_GROUPS（ADR 0009）：到顶禁用「添加条件组」并提示。
 *
 * 城市挂到组上（2026-09-13 grill 定稿，ADR 0019）：城市是**每个查询组**的属性，不是方向级单值。
 * `["Java","后端"]` 配成都、`["产品"]` 配杭州各搜各的。cities 与 keywords **平行等长**——
 * 本编辑器保证两者同步增删（同一下标），组级城市这里是唯一真相源。
 *
 * 2026-09-01：label/description 经 Input.Wrapper 渲染（与 TextInput/NumberInput 内置
 * label/description 同一套 Mantine 机制），统一检索控制台各字段标签观感。
 */

export function QueryGroupsEditor({
  value,
  cities,
  onValueChange,
  onCitiesChange,
  cityOptions,
  citiesLoading = false,
  disabled = false,
  label,
  description,
}: {
  value: string[][]
  cities: string[]
  onValueChange: (groups: string[][]) => void
  onCitiesChange: (cities: string[]) => void
  cityOptions: string[]
  /** 城市库正在拉取（面板挂载时才发起）：置灰 + 占位提示，避免加载中误显示成空下拉。 */
  citiesLoading?: boolean
  disabled?: boolean
  label?: string
  description?: string
}) {
  const t = useT()

  // 挂载兜底拉城市库（方向面板每次展开都会重挂本编辑器）。经 getState 读取、不进依赖数组——
  // 重复拉是幂等的（同一份后端常量），换来「无论从哪个入口打开，下拉都有数据」。
  useEffect(() => {
    void useDirectionStore.getState().loadCities()
  }, [])

  /** 保证 cities 与 keywords 等长（新增组补空串，防越界）。 */
  const alignedCities = (len: number) =>
    Array.from({ length: len }, (_, i) => cities[i] ?? '')

  const setGroup = (index: number, words: string[]) => {
    const next = value.slice()
    next[index] = words
    onValueChange(next)
  }

  const setCity = (index: number, city: string) => {
    const next = alignedCities(value.length)
    next[index] = city
    onCitiesChange(next)
  }

  const removeGroup = (index: number) => {
    onValueChange(value.filter((_, i) => i !== index))
    onCitiesChange(alignedCities(value.length).filter((_, i) => i !== index))
  }

  const addGroup = () => {
    onValueChange([...value, []])
    onCitiesChange([...alignedCities(value.length), ''])
  }

  /** 已到组数上限（ADR 0009）：禁用添加按钮，加不出第 MAX+1 组。 */
  const atLimit = value.length >= MAX_KEYWORD_GROUPS

  const cityData = cityOptions.map((c) => ({ value: c, label: c }))

  const body = (
    <div className="query-groups-editor">
      {value.map((group, index) => (
        <div className="query-group-row" key={index}>
          <TagsInput
            value={group}
            onChange={(words) => setGroup(index, words)}
            placeholder={t('apply.directionGroupPlaceholder')}
            disabled={disabled}
            clearable
            size="xs"
            data={[]}
            className="query-group-input"
            styles={{ input: { minHeight: 'auto' } }}
            // 与同行城市 Select 同一考虑：下拉一旦有建议项，portal 会踩 useClickOutside 误收面板。
            // 现在 data=[] 不弹东西，但先把 withinPortal 关掉，将来加词库不必再查一遍这个坑。
            comboboxProps={{ withinPortal: false }}
          />
          <Select
            value={cities[index] || null}
            onChange={(v) => setCity(index, v ?? '')}
            data={cityData}
            placeholder={citiesLoading ? t('apply.directionCityLoading') : t('apply.directionCityPlaceholder')}
            disabled={disabled || (citiesLoading && cityData.length === 0)}
            searchable
            clearable={false}
            size="xs"
            className="query-group-city"
            // 下拉渲染在面板内（withinPortal:false）：否则点下拉选项在 body portal，
            // ToolTab 的 useClickOutside 会误判为「点击外部」把整个浮窗收回（bug 修复，
            // 与 FactsPanel 同一坑同一修法）。注意——回显走的是 value 即 query-group-city
            // 自身，与 withinPortal 无关：关闭 portal 不会让选完的值回显不出来。
            comboboxProps={{ withinPortal: false }}
            // 下拉画在面板内、脱离 fixed 浮窗的裁切链，故需自行给足层叠——
            // 否则会被下方「岗位获取」等模块盖住。100 高于面板/浮窗的 60。
            styles={{ dropdown: { zIndex: 100 } }}
            nothingFoundMessage={t('apply.directionCityNoMatch')}
          />
          <ActionIcon
            variant="default"
            color="gray"
            onClick={() => removeGroup(index)}
            disabled={disabled || value.length <= 1}
            aria-label={t('apply.directionGroupRemove')}
          >
            <X size={14} />
          </ActionIcon>
        </div>
      ))}
      <div className="query-group-add-row">
        <Button
          variant="default"
          size="compact-sm"
          leftSection={<Plus size={14} />}
          onClick={addGroup}
          disabled={disabled || atLimit}
          className="query-group-add"
        >
          {t('apply.directionGroupAdd')}
        </Button>
        {atLimit && (
          <span className="query-group-limit-hint">
            {t('apply.directionGroupLimit').replace('{max}', String(MAX_KEYWORD_GROUPS))}
          </span>
        )}
      </div>
    </div>
  )

  if (!label && !description) return body
  return (
    <Input.Wrapper label={label} description={description}>
      {body}
    </Input.Wrapper>
  )
}
