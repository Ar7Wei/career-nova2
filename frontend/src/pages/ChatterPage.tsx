import { useT } from '@/lib/i18n'
import { ComingSoonCard } from '@/components/layout/ComingSoonCard'

export function ChatterPage() {
  const t = useT()
  return (
    <div className="coming-soon-page">
      <div className="page-header">
        <h1 className="page-title">{t('page.chatter.title')}</h1>
        <p className="page-subtitle">{t('page.chatter.desc')}</p>
      </div>
      <ComingSoonCard />
    </div>
  )
}
