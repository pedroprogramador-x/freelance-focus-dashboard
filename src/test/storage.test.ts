import { beforeEach, describe, expect, it } from 'vitest'
import { createDefaultData } from '../data/defaults'
import { exportBackup, loadData, migrateData, parseBackup, saveData, STORAGE_KEY } from '../services/storage'

describe('persistência e backup', () => {
  beforeEach(() => localStorage.clear())

  it('salva e carrega usando uma única chave versionada', () => {
    const data = createDefaultData('2026-03-01')
    data.settings.userName = 'Pedro'
    saveData(data)
    expect(localStorage.length).toBe(1)
    expect(localStorage.getItem(STORAGE_KEY)).toBeTruthy()
    expect(loadData().settings.userName).toBe('Pedro')
  })

  it('se recupera de dados corrompidos', () => {
    localStorage.setItem(STORAGE_KEY, '{dados quebrados')
    expect(loadData().tasks).toHaveLength(90)
  })

  it('rejeita estruturas internas corrompidas mesmo com arrays presentes', () => {
    const corrupted = createDefaultData('2026-03-01')
    corrupted.tasks[0] = null as never
    localStorage.setItem(STORAGE_KEY, JSON.stringify(corrupted))
    expect(loadData().tasks[0].id).toBe('task-01')
  })

  it('migra dados legados e completa configurações ausentes', () => {
    const legacy = createDefaultData('2026-03-01') as unknown as Record<string, unknown>
    legacy.schemaVersion = 0
    const settings = legacy.settings as Record<string, unknown>
    delete settings.confirmBeforeDelete
    const migrated = migrateData(legacy)
    expect(migrated?.schemaVersion).toBe(1)
    expect(migrated?.settings.confirmBeforeDelete).toBe(true)
  })

  it('exporta, importa e rejeita backup inválido', () => {
    const data = createDefaultData('2026-03-01')
    const backup = exportBackup(data)
    expect(parseBackup(JSON.stringify(backup)).tasks).toHaveLength(90)
    expect(() => parseBackup('{"qualquer":true}')).toThrow('backup válido')
  })
})
