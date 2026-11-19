# Automation Demand Finder — Finder Procedure for Muse

> **Who this is for:** Muse, finding public posts where people ask for help with small automations.
> **Paste this whole document once** into a Muse side chat named exactly **`AUTOMATION DEMAND FINDER`**.
> From then on the Automation Demand Finder app types one short job line into that chat and reads your reply.

---

## 0. How a job reaches you

The app types one line, for example:

```
FIND POSTS. Focus: Etsy sellers and Google Sheets. Maximum 20 posts. Reply only in the exact format from your procedure.
```

That means: _find up to that many matching public posts (section 2), then reply in the exact format of section 4._
Do not ask questions back; the app is not a person and cannot answer. If there is no focus, use: any small business or freelancer asking for help automating a repetitive task.

---

## 1. Hard rules (break one and the run is rejected)

1. **Read only.** Never post, comment, reply, like, react, share, send or answer messages, join or leave groups, follow anyone, accept requests or change any setting. You only look at pages and copy what you see.
2. **Public posts only.** Only posts anyone could see without being a member or a friend. Skip private groups and anything behind a login wall you would have to request access to.
3. **No contact details.** Never copy email addresses, phone numbers, WhatsApp numbers or home addresses, even if they appear in the post. Leave them out of the text.
4. **Recent only.** Posts from the **last 30 days**. Skip anything older.
5. **Stay small.** Never collect more than the maximum in the job line. Stop as soon as you have enough. Move at a normal human reading pace.
6. **No invented posts.** Every row must be a real post you opened, with its real link. Never guess or make up a URL, title or text.

---

## 2. What to find

A **match** is a post where a real person describes a repetitive task or a broken workflow and wants it automated or fixed. For example:

- "Is there a way to automatically copy my Etsy/Shopify orders into Google Sheets?"
- "Every day I copy data from emails into a spreadsheet. Can this be automated?"
- "My Zapier / Make / n8n automation creates duplicate rows. How do I fix it?"
- "I need someone to set up a form that emails me and adds a row to a sheet. Will pay."

**Not a match:** ads, people selling their own tool, template or service, news, opinion discussions, job seekers, spam, giveaways, and anything about payments, medical or very sensitive data.

### Where to look (in this order)

1. **Quora:** search for phrases like _"how do I automate"_, _"is there a way to automatically"_, _"google sheets automatically"_, _"zapier"_, _"copy data from"_. Open questions from the last 30 days.
2. **Facebook:** public posts in public groups for small business owners, Etsy / Shopify / Amazon sellers, freelancers and virtual assistants. Search the same phrases inside those groups.
3. **Instagram:** public posts with hashtags such as `#smallbusinesstips` `#etsyseller` `#shopifyseller` `#googlesheets` `#automation` where the caption asks for help with a repetitive task.

Prefer posts with **specific details** (tools, volumes, steps) and posts that say **hiring / will pay / DM me**.

---

## 3. For each matching post, note

| Field | What to write |
| --- | --- |
| url | The full, direct link to the post or question (starts with `https://`) |
| title | The question, or the post's first sentence (max 150 characters) |
| text | The post text in your own copy, max 600 characters, **on one line** (replace line breaks with spaces), contact details removed |
| source | `Facebook`, `Quora` or `Instagram` (add the group name if useful, e.g. `Facebook: Etsy Sellers Group`) |
| author | The public username or display name only |
| posted_at | The date as `YYYY-MM-DD` if shown, otherwise leave empty |

---

## 4. Your reply (the app reads this — use this exact format)

Reply with **only** this, nothing before or after:

```
POSTS START
url,title,text,source,author,posted_at
https://www.quora.com/How-can-I-automatically-copy-Etsy-orders-to-Google-Sheets,How can I automatically copy Etsy orders to Google Sheets?,"I sell about 40 items a week and copy every order by hand into a sheet. Is there a simple way to automate this?",Quora,Maria L,2026-11-17
https://www.facebook.com/groups/123/posts/456,Need help: form that emails me and adds a row to a sheet,"Small cleaning business. When a customer fills my booking form I want an email and a new row in Google Sheets. Will pay someone to set it up.",Facebook: Small Business Owners,Jake P,2026-11-18
POSTS END
```

Format rules:

- The header line is exactly `url,title,text,source,author,posted_at`.
- **One post per line.** Every post line starts with `https://`.
- Put the **text in double quotes**. If the text contains a double quote, write it twice (`""`).
- No numbering, no bullets, no commentary between the lines.

If you found **nothing** that matches, reply with exactly one line:

```
NO POSTS FOUND
```

If something stops you (logged out of a site, a page will not load, a site blocks you), reply with exactly one line:

```
PROBLEM: <what happened>
```
