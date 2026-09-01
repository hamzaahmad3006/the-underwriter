import type { ReactNode } from 'react'

export interface Column<T> {
  key: string
  header: string
  /** Money and Greeks are numeric: right-aligned, tabular-nums (UI-007, UI-015). */
  numeric?: boolean
  render: (row: T) => ReactNode
}

/**
 * The dashboard's only table.
 *
 * The wrapper scrolls horizontally on its own so a wide book never makes the
 * page body scroll sideways on a phone (UI-008).
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  empty,
}: {
  columns: Column<T>[]
  rows: T[]
  rowKey: (row: T) => string
  empty?: ReactNode
}) {
  if (rows.length === 0 && empty) return <>{empty}</>

  return (
    <div className="-mx-4 overflow-x-auto px-4">
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr className="border-line border-b">
            {columns.map((column) => (
              <th
                key={column.key}
                className={`text-muted pb-2 text-[11px] font-medium tracking-wide uppercase ${
                  column.numeric ? 'text-right' : 'text-left'
                }`}
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className="border-line/60 border-b last:border-0">
              {columns.map((column) => (
                <td
                  key={column.key}
                  className={`py-2 ${column.numeric ? 'num text-right' : 'text-left'}`}
                >
                  {column.render(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
