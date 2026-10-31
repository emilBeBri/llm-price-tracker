# A Mintlify page's .md rendering can ship EMPTY tables while the HTML still has the data
tags = #moonshot #gotcha #scraping

platform.kimi.ai is Mintlify. `docs/pricing/chat.md` is advertised in the page's
`<link rel="alternate" type="text/markdown">`, and for months it carried the
price table as a server-side `<DocTable>` MDX block — the only reason
`MoonshotSource` read markdown rather than HTML.

Since the K3.1 rewrite that rendering contains:

```html
<table>
  <thead><tr /></thead>
  <tbody />
</table>
```

The rows are not stripped by a mirror and not hidden behind JS in the `.md`; they
are simply not rendered there. `.mdx` and `.k3.md` both redirect to a generic
page. `check` reported "parsed 0 models — page structure changed", which is the
good failure, but the page looks perfect in a browser, so the obvious conclusion
("the vendor broke something") is wrong: the data is one layer down.

What the browser actually receives is the page HTML, whose Next.js RSC payload
holds the table as DATA:

    _jsx(DocTable,{columns:[{title:`Model`,width:`25%`},…],rows:[[…]]})

with cells as backticked strings (`$2.00 / MTok`), and column labels either a
bare `title:` or a `children:` inside a `Tooltip`. Two structural facts that make
the walk safe: `_jsx(DocTable,{…})` ends at `]]})` (no cell can produce that
sequence), and every column object closes with `,width:`NN%`}`, which keeps a
Tooltip's tip text in the same chunk as the label it wraps.

The generalizable move, worth trying before declaring a docs source dead: fetch
the HTML and grep it for the model id. A client-rendered table is still in the
payload that renders it. Same family as [[wrong-document-200]] — the URL answered
200 and served something that is not the page you asked for — and the reason
[[parse-the-table-not-the-heading]] insists on shape over position.

# sources

conversation 2026-10-31 — getting K3.1 Pro rates into the book
