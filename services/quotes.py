"""One investor quote a day on the home page (same quote for everyone that day, US Eastern date). No network, no AI.
Short, well-documented quotations only; where the wording is the commonly cited form, the source notes say so."""
from __future__ import annotations

from datetime import date

QUOTES: list[tuple[str, str, str]] = [   # (quote, who, where)
    ("Price is what you pay. Value is what you get.", "Warren Buffett", "Berkshire Hathaway letter, 2008"),
    ("We simply attempt to be fearful when others are greedy and to be greedy only when others are fearful.",
     "Warren Buffett", "Berkshire Hathaway letter, 1986"),
    ("Our favorite holding period is forever.", "Warren Buffett", "Berkshire Hathaway letter, 1988"),
    ("It's far better to buy a wonderful company at a fair price than a fair company at a wonderful price.",
     "Warren Buffett", "Berkshire Hathaway letter, 1989"),
    ("Time is the friend of the wonderful business, the enemy of the mediocre.", "Warren Buffett",
     "Berkshire Hathaway letter, 1989"),
    ("Only when the tide goes out do you discover who's been swimming naked.", "Warren Buffett",
     "Berkshire Hathaway letter, 2001"),
    ("If you aren't willing to own a stock for ten years, don't even think about owning it for ten minutes.",
     "Warren Buffett", "Berkshire Hathaway letter, 1996"),
    ("Opportunities come infrequently. When it rains gold, put out the bucket, not the thimble.", "Warren Buffett",
     "Berkshire Hathaway letter, 2009"),
    ("Risk comes from not knowing what you're doing.", "Warren Buffett", ""),
    ("In the short run, the market is a voting machine but in the long run, it is a weighing machine.",
     "Benjamin Graham", "as quoted by Warren Buffett"),
    ("The investor's chief problem, and even his worst enemy, is likely to be himself.", "Benjamin Graham",
     "The Intelligent Investor"),
    ("Confronted with a challenge to distill the secret of sound investment into three words, we venture the motto, "
     "Margin of Safety.", "Benjamin Graham", "The Intelligent Investor"),
    ("The individual investor should act consistently as an investor and not as a speculator.", "Benjamin Graham",
     "The Intelligent Investor"),
    ("Know what you own, and know why you own it.", "Peter Lynch", ""),
    ("Behind every stock is a company. Find out what it's doing.", "Peter Lynch", ""),
    ("Go for a business that any idiot can run, because sooner or later any idiot probably is going to run it.",
     "Peter Lynch", ""),
    ("Far more money has been lost by investors preparing for corrections, or trying to anticipate corrections, "
     "than has been lost in corrections themselves.", "Peter Lynch", ""),
    ("In this business, if you're good, you're right six times out of ten.", "Peter Lynch", ""),
    ("The big money is not in the buying and selling, but in the waiting.", "Charlie Munger", ""),
    ("Invert, always invert.", "Charlie Munger", "after the mathematician Carl Jacobi"),
    ("Spend each day trying to be a little wiser than you were when you woke up.", "Charlie Munger", ""),
    ("Bull markets are born on pessimism, grow on skepticism, mature on optimism, and die on euphoria.",
     "John Templeton", ""),
    ("The time of maximum pessimism is the best time to buy.", "John Templeton", ""),
    ("The four most dangerous words in investing are: this time it's different.", "John Templeton", "commonly cited form"),
    ("The stock market is filled with individuals who know the price of everything, but the value of nothing.",
     "Philip Fisher", "commonly cited form"),
    ("You can't predict. You can prepare.", "Howard Marks", ""),
    ("Being too far ahead of your time is indistinguishable from being wrong.", "Howard Marks", ""),
    ("Value investing is at its core the marriage of a contrarian streak and a calculator.", "Seth Klarman", ""),
    ("Buy good companies. Don't overpay. Do nothing.", "Terry Smith", "Fundsmith"),
    ("Heads, I win; tails, I don't lose much.", "Mohnish Pabrai", "The Dhandho Investor"),
    ("Investing should be more like watching paint dry or watching grass grow.", "Paul Samuelson", ""),
    ("In investing, what is comfortable is rarely profitable.", "Robert Arnott", ""),
    ("It's not whether you're right or wrong that's important, but how much money you make when you're right "
     "and how much you lose when you're wrong.", "George Soros", ""),
]


def for_day(day: date | None = None) -> tuple[str, str, str]:
    day = day or date.today()
    return QUOTES[day.toordinal() % len(QUOTES)]
