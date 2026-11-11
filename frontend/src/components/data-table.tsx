import {
  flexRender, getCoreRowModel, useReactTable, type ColumnDef, type Row, type RowData,
} from '@tanstack/react-table';
import type * as React from 'react';
import { cn } from '@/lib/utils';
import { Skeleton } from './ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from './ui/table';

declare module '@tanstack/react-table' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    /** Classes for th/td, e.g. `hidden md:table-cell` to drop a column on phones, or `text-right`. */
    className?: string;
  }
}

/**
 * Data table on TanStack Table + shadcn table markup. Pagination stays on the server
 * (see Pager); this renders rows, a loading skeleton and the empty state.
 */
export function DataTable<T>({
  columns,
  data,
  loading = false,
  empty,
  getRowId,
  rowClassName,
  onRowClick,
  caption,
}: {
  columns: ColumnDef<T, unknown>[];
  data: T[];
  loading?: boolean;
  empty?: React.ReactNode;
  getRowId?: (row: T) => string;
  rowClassName?: (row: Row<T>) => string | undefined;
  onRowClick?: (row: T) => void;
  caption?: string;
}) {
  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel(), getRowId });
  const rows = table.getRowModel().rows;
  return (
    <Table>
      {caption && <caption className="sr-only">{caption}</caption>}
      <TableHeader>
        {table.getHeaderGroups().map((hg) => (
          <TableRow key={hg.id} className="hover:bg-transparent">
            {hg.headers.map((h) => (
              <TableHead key={h.id} className={h.column.columnDef.meta?.className}>
                {h.isPlaceholder ? null : flexRender(h.column.columnDef.header, h.getContext())}
              </TableHead>
            ))}
          </TableRow>
        ))}
      </TableHeader>
      <TableBody>
        {loading && rows.length === 0 ? (
          Array.from({ length: 5 }, (_, i) => (
            <TableRow key={`s${i}`} className="hover:bg-transparent">
              <TableCell colSpan={columns.length}>
                <Skeleton className="h-6 w-full" />
              </TableCell>
            </TableRow>
          ))
        ) : rows.length === 0 ? (
          <TableRow className="hover:bg-transparent">
            <TableCell colSpan={columns.length}>{empty}</TableCell>
          </TableRow>
        ) : (
          rows.map((row) => (
            <TableRow
              key={row.id}
              className={cn(onRowClick && 'cursor-pointer', rowClassName?.(row))}
              onClick={onRowClick ? () => onRowClick(row.original) : undefined}
            >
              {row.getVisibleCells().map((cell) => (
                <TableCell key={cell.id} className={cell.column.columnDef.meta?.className}>
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );
}
