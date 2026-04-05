"""
Tests for trend_skills.py — LLM-callable trend detection functions.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skills import trend_skills as mod


@pytest.fixture
def mock_tracker():
    """Mock TrendTracker for testing."""
    with patch("skills.trend_skills.get_tracker") as mock_get:
        tracker = MagicMock()
        mock_get.return_value = tracker
        yield tracker


@pytest.mark.asyncio
async def test_track_topic_success(mock_tracker):
    """Test successful topic tracking."""
    mock_tracker.enable_tracking.return_value = True
    
    with patch("skills.trend_skills._collect_data_point", new_callable=AsyncMock) as mock_collect:
        mock_collect.return_value = True
        
        result = await mod.track_topic("Bitcoin", "Finance", "user123")
        
        assert result["status"] == "ok"
        assert "Bitcoin" in result["message"]
        assert result["topic"] == "Bitcoin"
        assert result["category"] == "Finance"
        
        mock_tracker.enable_tracking.assert_called_once_with(
            "Bitcoin", "Finance", "user123"
        )
        mock_collect.assert_called_once_with("Bitcoin", "Finance")


@pytest.mark.asyncio
async def test_track_topic_failure(mock_tracker):
    """Test failed topic tracking."""
    mock_tracker.enable_tracking.return_value = False
    
    result = await mod.track_topic("Bitcoin", "Finance")
    
    assert result["status"] == "error"
    assert "Failed" in result["message"]


@pytest.mark.asyncio
async def test_untrack_topic_success(mock_tracker):
    """Test successful topic untracking."""
    mock_tracker.disable_tracking.return_value = True
    
    result = await mod.untrack_topic("Bitcoin")
    
    assert result["status"] == "ok"
    assert "Bitcoin" in result["message"]
    mock_tracker.disable_tracking.assert_called_once_with("Bitcoin")


@pytest.mark.asyncio
async def test_untrack_topic_failure(mock_tracker):
    """Test failed topic untracking."""
    mock_tracker.disable_tracking.return_value = False
    
    result = await mod.untrack_topic("Bitcoin")
    
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_get_trending_topics(mock_tracker):
    """Test getting trending topics."""
    # Mock TrendAnalysis object
    mock_analysis = MagicMock()
    mock_analysis.topic = "Bitcoin"
    mock_analysis.category = "Finance"
    mock_analysis.current_volume = 50
    mock_analysis.volume_change_pct = 380.0
    mock_analysis.current_sentiment = 0.82
    mock_analysis.sentiment_change_24h = 0.15
    mock_analysis.trend_direction = "up"
    mock_analysis.is_spike = True
    mock_analysis.is_breakout = False
    mock_analysis.velocity = 4.2
    mock_analysis.z_score = 3.5
    mock_analysis.sources = ["NewsAPI", "Alpha Vantage"]
    
    mock_tracker.get_trending_topics.return_value = [mock_analysis]
    
    result = await mod.get_trending_topics("Finance", "24h", 10)
    
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["timeframe"] == "24h"
    assert result["category"] == "Finance"
    
    topics = result["trending_topics"]
    assert len(topics) == 1
    assert topics[0]["topic"] == "Bitcoin"
    assert topics[0]["volume"] == 50
    assert topics[0]["volume_change"] == "+380%"
    assert topics[0]["sentiment"] == 0.82
    assert topics[0]["is_spike"] is True
    
    mock_tracker.get_trending_topics.assert_called_once_with("Finance", 24, 10)


@pytest.mark.asyncio
async def test_get_trending_topics_different_timeframes(mock_tracker):
    """Test different timeframe parameters."""
    mock_tracker.get_trending_topics.return_value = []
    
    # Test 24h
    await mod.get_trending_topics(timeframe="24h")
    mock_tracker.get_trending_topics.assert_called_with("", 24, 10)
    
    # Test 7d
    await mod.get_trending_topics(timeframe="7d")
    mock_tracker.get_trending_topics.assert_called_with("", 168, 10)
    
    # Test 30d
    await mod.get_trending_topics(timeframe="30d")
    mock_tracker.get_trending_topics.assert_called_with("", 720, 10)


@pytest.mark.asyncio
async def test_detect_breaking_news(mock_tracker):
    """Test breaking news detection."""
    # Mock spike analysis
    mock_analysis = MagicMock()
    mock_analysis.topic = "Moana 2"
    mock_analysis.category = "Entertainment"
    mock_analysis.current_volume = 47
    mock_analysis.avg_volume_7d = 10.0
    mock_analysis.current_sentiment = 0.82
    mock_analysis.is_spike = True
    mock_analysis.peak_time = 1234567890.0
    mock_analysis.z_score = 4.2
    
    mock_tracker.get_trending_topics.return_value = [mock_analysis]
    
    # Mock datetime directly in the module
    with patch("skills.trend_skills.datetime") as mock_dt:
        mock_dt.now.return_value.timestamp.return_value = 1234575090.0  # 2 hours later
        mock_dt.fromtimestamp.return_value.timestamp.return_value = 1234567890.0
        
        result = await mod.detect_breaking_news("Entertainment", spike_threshold=3.0)
        
        assert result["status"] == "ok"
        assert result["count"] == 1
        
        breaking = result["breaking_news"]
        assert len(breaking) == 1
        assert breaking[0]["topic"] == "Moana 2"
        assert breaking[0]["volume"] == 47
        assert breaking[0]["spike_multiplier"] == 4.7


@pytest.mark.asyncio
async def test_detect_breaking_news_filters_below_threshold(mock_tracker):
    """Test that breaking news filters out items below threshold."""
    # Mock analysis with spike below threshold
    mock_analysis = MagicMock()
    mock_analysis.topic = "LowSpike"
    mock_analysis.current_volume = 10
    mock_analysis.avg_volume_7d = 5.0
    mock_analysis.is_spike = True
    mock_analysis.peak_time = 1234567890.0
    
    mock_tracker.get_trending_topics.return_value = [mock_analysis]
    
    result = await mod.detect_breaking_news(spike_threshold=5.0)
    
    # Should filter out (2x < 5x threshold)
    assert result["count"] == 0
    assert len(result["breaking_news"]) == 0


@pytest.mark.asyncio
async def test_get_topic_trajectory(mock_tracker):
    """Test getting topic trajectory."""
    # Mock analysis
    mock_analysis = MagicMock()
    mock_analysis.topic = "Bitcoin"
    mock_analysis.category = "Finance"
    mock_analysis.current_volume = 47
    mock_analysis.volume_change_pct = 380.0
    mock_analysis.current_sentiment = 0.82
    mock_analysis.sentiment_change_24h = 0.15
    mock_analysis.is_trending = True
    mock_analysis.is_spike = True
    mock_analysis.is_breakout = False
    mock_analysis.trend_direction = "up"
    mock_analysis.velocity = 4.2
    mock_analysis.z_score = 3.5
    
    mock_tracker.is_trending.return_value = mock_analysis
    
    with patch("skills.trend_skills.render_text_chart") as mock_chart, \
         patch("skills.trend_skills._generate_analysis_text") as mock_analysis_text:
        
        mock_chart.return_value = "📊 Chart here"
        mock_analysis_text.return_value = "Bitcoin is trending up"
        
        result = await mod.get_topic_trajectory("Bitcoin", "Finance", "24h")
        
        assert result["status"] == "ok"
        assert result["topic"] == "Bitcoin"
        assert result["category"] == "Finance"
        assert result["current_volume"] == 47
        assert result["volume_change"] == "+380%"
        assert result["sentiment"] == 0.82
        assert result["is_trending"] is True
        assert result["chart"] == "📊 Chart here"
        assert result["analysis"] == "Bitcoin is trending up"


@pytest.mark.asyncio
async def test_get_topic_trajectory_no_data(mock_tracker):
    """Test trajectory for topic with no data."""
    # Mock empty analysis
    mock_analysis = MagicMock()
    mock_analysis.current_volume = 0
    
    mock_tracker.is_trending.return_value = mock_analysis
    
    result = await mod.get_topic_trajectory("UnknownTopic", "Finance")
    
    assert result["status"] == "error"
    assert "No data" in result["message"]


@pytest.mark.asyncio
async def test_list_tracked_topics(mock_tracker):
    """Test listing tracked topics."""
    import time
    now = time.time()
    
    mock_tracker.get_tracked_topics.return_value = [
        {
            "topic": "Bitcoin",
            "category": "Finance",
            "enabled": 1,
            "created_at": now,
            "spike_threshold": 3.0,
            "sentiment_threshold": 0.3,
        },
        {
            "topic": "Lakers",
            "category": "Sports",
            "enabled": 1,
            "created_at": now - 3600,
            "spike_threshold": 3.0,
            "sentiment_threshold": 0.3,
        },
    ]
    
    result = await mod.list_tracked_topics()
    
    assert result["status"] == "ok"
    assert result["count"] == 2
    
    topics = result["tracked_topics"]
    assert len(topics) == 2
    assert topics[0]["topic"] == "Bitcoin"
    assert topics[0]["enabled"] is True
    assert topics[1]["topic"] == "Lakers"


@pytest.mark.asyncio
async def test_collect_data_point_news(mock_tracker):
    """Test collecting data from NewsAPI."""
    with patch("skills.trend_skills.cfg") as mock_cfg, \
         patch("skills.trend_skills.news_skills.search_news", new_callable=AsyncMock) as mock_news:
        
        mock_cfg.newsapi_key = "test_key"
        mock_cfg.alphavantage_key = None
        mock_cfg.apisports_key = None
        
        mock_news.return_value = {
            "status": "ok",
            "articles": [
                {"title": "Bitcoin rises", "description": "Gains made"},
                {"title": "Success story", "description": "New high"},
            ],
        }
        
        result = await mod._collect_data_point("Bitcoin", "Finance")
        
        assert result is True
        mock_tracker.track_entity.assert_called_once()
        
        # Check call arguments
        call_args = mock_tracker.track_entity.call_args
        assert call_args[0][0] == "Bitcoin"  # topic
        assert call_args[0][1] == "Finance"  # category
        assert call_args[0][2] == 2  # volume (2 articles)
        assert "NewsAPI" in call_args[0][4]  # sources


@pytest.mark.asyncio
async def test_collect_data_point_finance(mock_tracker):
    """Test collecting data from Alpha Vantage."""
    with patch("skills.trend_skills.cfg") as mock_cfg, \
         patch("skills.trend_skills.finance_skills.get_stock_info", new_callable=AsyncMock) as mock_stock, \
         patch("skills.trend_skills.news_skills.search_news", new_callable=AsyncMock) as mock_news:
        
        mock_cfg.newsapi_key = "test_key"
        mock_cfg.alphavantage_key = "test_key"
        
        mock_news.return_value = {"status": "ok", "articles": []}
        mock_stock.return_value = {
            "status": "ok",
            "change_percent": "+5.2%",
        }
        
        result = await mod._collect_data_point("AAPL", "Finance")
        
        assert result is True
        
        call_args = mock_tracker.track_entity.call_args
        assert "Alpha Vantage" in call_args[0][4]  # sources


@pytest.mark.asyncio
async def test_calculate_simple_sentiment():
    """Test simple sentiment calculation."""
    articles = [
        {"title": "Success and win", "description": "Best record high"},
        {"title": "Decline and loss", "description": "Worst crash"},
        {"title": "Neutral news", "description": "Normal day"},
    ]
    
    sentiment = mod._calculate_simple_sentiment(articles)
    
    # Should be close to neutral (mix of positive and negative)
    assert -1.0 <= sentiment <= 1.0


@pytest.mark.asyncio
async def test_calculate_simple_sentiment_positive():
    """Test positive sentiment."""
    articles = [
        {"title": "Success win gain", "description": "Rise up breakthrough"},
        {"title": "Record high best", "description": "Up up up"},
    ]
    
    sentiment = mod._calculate_simple_sentiment(articles)
    
    assert sentiment > 0.5


@pytest.mark.asyncio
async def test_calculate_simple_sentiment_negative():
    """Test negative sentiment."""
    articles = [
        {"title": "Fail loss crash", "description": "Down decline worst"},
        {"title": "Drop low fail", "description": "Down down"},
    ]
    
    sentiment = mod._calculate_simple_sentiment(articles)
    
    assert sentiment < -0.5


def test_generate_analysis_text_spike():
    """Test analysis text generation for spike."""
    mock_analysis = MagicMock()
    mock_analysis.topic = "Bitcoin"
    mock_analysis.is_spike = True
    mock_analysis.is_breakout = False
    mock_analysis.is_trending = True
    mock_analysis.trend_direction = "up"
    mock_analysis.volume_change_pct = 380.0
    mock_analysis.current_sentiment = 0.82
    mock_analysis.sentiment_change_24h = 0.15
    mock_analysis.velocity = 4.2
    
    text = mod._generate_analysis_text(mock_analysis)
    
    assert "spike" in text.lower()
    assert "Bitcoin" in text
    assert "380%" in text


def test_generate_analysis_text_breakout():
    """Test analysis text generation for breakout."""
    mock_analysis = MagicMock()
    mock_analysis.topic = "NewTopic"
    mock_analysis.is_spike = False
    mock_analysis.is_breakout = True
    mock_analysis.is_trending = True
    mock_analysis.trend_direction = "up"
    mock_analysis.volume_change_pct = 100.0
    mock_analysis.current_sentiment = 0.6
    mock_analysis.velocity = 2.5
    
    text = mod._generate_analysis_text(mock_analysis)
    
    assert "new" in text.lower() or "breakout" in text.lower()


@pytest.mark.asyncio
async def test_update_all_tracked_trends(mock_tracker):
    """Test background job for updating all trends."""
    mock_tracker.get_tracked_topics.return_value = [
        {"topic": "Bitcoin", "category": "Finance"},
        {"topic": "Lakers", "category": "Sports"},
    ]
    
    mock_tracker.cleanup_old_data.return_value = 10
    
    with patch("skills.trend_skills._collect_data_point", new_callable=AsyncMock) as mock_collect:
        mock_collect.return_value = True
        
        result = await mod.update_all_tracked_trends()
        
        assert "Updated: 2" in result
        assert "Failed: 0" in result
        assert "Cleaned: 10" in result
        
        assert mock_collect.call_count == 2
        mock_tracker.cleanup_old_data.assert_called_once()


@pytest.mark.asyncio
async def test_update_all_tracked_trends_with_failures(mock_tracker):
    """Test background job with some failures."""
    mock_tracker.get_tracked_topics.return_value = [
        {"topic": "Bitcoin", "category": "Finance"},
        {"topic": "FailTopic", "category": "News"},
    ]
    
    mock_tracker.cleanup_old_data.return_value = 5
    
    with patch("skills.trend_skills._collect_data_point", new_callable=AsyncMock) as mock_collect:
        # First call succeeds, second fails
        mock_collect.side_effect = [True, False]
        
        result = await mod.update_all_tracked_trends()
        
        assert "Updated: 1" in result
        assert "Failed: 1" in result
