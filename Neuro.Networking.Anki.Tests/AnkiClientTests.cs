using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.VisualStudio.TestTools.UnitTesting;
using Waher.Networking.Sniffers;

namespace Neuro.Networking.Anki.Tests;

[TestClass]
public class AnkiClientTests
{
    private const string Token = "test-bridge-token";

    [TestMethod]
    public async Task ListNotesEscapesFiltersAndMapsSnakeCase()
    {
        using var Http = new HttpClient(new Handler(async (Request, Cancel) =>
        {
            Assert.AreEqual("Bearer", Request.Headers.Authorization!.Scheme);
            Assert.AreEqual(Token, Request.Headers.Authorization.Parameter);
            Assert.AreEqual("https://example.test/prefix/v1/notes?after_id=41&limit=2&deck=Swedish%20%26%20French&tag=vocab%2Fverbs", Request.RequestUri!.AbsoluteUri);
            await Task.Yield();
            return Json("{\"items\":[{\"id\":42,\"guid\":\"g\",\"model\":\"Basic\",\"modified_seconds\":100,\"fields\":{\"Front\":\"hej\"},\"tags\":[\"vocab\"]}],\"next_cursor\":42}");
        }));
        using var Client = new AnkiClient("https://example.test/prefix", Token, Http);
        var Page = await Client.GetNotesAsync(41, 2, "Swedish & French", "vocab/verbs");
        Assert.AreEqual(42L, Page.NextCursor);
        Assert.AreEqual(100L, Page.Items[0].ModifiedSeconds);
        Assert.AreEqual("hej", Page.Items[0].Fields["Front"]);
    }

    [TestMethod]
    public async Task CreateAndUpdateKeepAnkiFieldNamesAndOmitNullTags()
    {
        int Calls = 0;
        using var Http = new HttpClient(new Handler(async (Request, Cancel) =>
        {
            using var Body = JsonDocument.Parse(await Request.Content!.ReadAsStringAsync(Cancel));
            Assert.AreEqual("hej", Body.RootElement.GetProperty("fields").GetProperty("Front").GetString());
            if (Calls++ == 0)
            {
                Assert.AreEqual(HttpMethod.Post, Request.Method);
                Assert.AreEqual("Basic", Body.RootElement.GetProperty("model").GetString());
            }
            else
            {
                Assert.AreEqual("PATCH", Request.Method.Method);
                Assert.IsFalse(Body.RootElement.TryGetProperty("tags", out _));
            }
            return Json("{\"id\":42,\"fields\":{\"Front\":\"hej\"}}");
        }));
        using var Client = new AnkiClient("http://127.0.0.1:8766", Token, Http);
        await Client.CreateNoteAsync(new CreateNote { Fields = new() { ["Front"] = "hej" } });
        await Client.UpdateNoteAsync(42, new UpdateNote { Fields = new() { ["Front"] = "hej" } });
        Assert.AreEqual(2, Calls);
    }

    [TestMethod]
    public async Task DeleteAcceptsNoContentAndReadsPreserveSignedIntervals()
    {
        using var Http = new HttpClient(new Handler((Request, Cancel) => Task.FromResult(
            Request.Method == HttpMethod.Delete ? new HttpResponseMessage(HttpStatusCode.NoContent) :
            Json("{\"items\":[{\"id\":1700000000001,\"card_id\":42,\"interval\":1,\"last_interval\":-60,\"duration_ms\":500}],\"next_cursor\":null}"))));
        using var Client = new AnkiClient("https://example.test/", Token, Http);
        await Client.DeleteNoteAsync(42);
        var Page = await Client.GetReviewsAsync(CardId: 42);
        Assert.AreEqual(-60, Page.Items[0].LastInterval);
        Assert.AreEqual(500, Page.Items[0].DurationMs);
        Assert.IsNull(Page.NextCursor);
    }

    [TestMethod]
    public async Task SyncRequiresSeparateExplicitDownloadCall()
    {
        var Modes = new List<string>();
        using var Http = new HttpClient(new Handler(async (Request, Cancel) =>
        {
            Assert.AreEqual("/v1/sync", Request.RequestUri!.AbsolutePath);
            using var Body = JsonDocument.Parse(await Request.Content!.ReadAsStringAsync(Cancel));
            Modes.Add(Body.RootElement.GetProperty("mode").GetString()!);
            return Json("{\"status\":\"mock_noop\",\"mode\":\"incremental\"}");
        }));
        using var Client = new AnkiClient("https://example.test/", Token, Http);
        await Client.SyncAsync();
        await Client.DownloadCollectionAsync();
        CollectionAssert.AreEqual(new[] { "incremental", "download" }, Modes);
    }

    [TestMethod]
    public async Task ErrorIsTypedAndSniffersExcludeTokenQueryAndBody()
    {
        using var Output = new StringWriter();
        await using var Sniffer = new TextWriterSniffer(Output, BinaryPresentationMethod.Base64, "Anki test");
        using var Http = new HttpClient(new Handler((Request, Cancel) => Task.FromResult(
            new HttpResponseMessage(HttpStatusCode.Conflict) { Content = new StringContent("{\"detail\":{\"code\":\"full_sync_required\"}}") })));
        using var Client = new AnkiClient("https://example.test/", Token, Http, Sniffer);
        var Error = await Assert.ThrowsExactlyAsync<AnkiApiException>(() => Client.GetNotesAsync(Deck: "private-deck"));
        Assert.AreEqual(HttpStatusCode.Conflict, Error.StatusCode);
        StringAssert.Contains(Error.ResponseBody, "full_sync_required");
        Assert.IsFalse(Error.Message.Contains("full_sync_required"));
        await Sniffer.FlushAsync();
        string Log = Output.ToString();
        StringAssert.Contains(Log, "GET v1/notes");
        StringAssert.Contains(Log, "HTTP 409");
        Assert.IsFalse(Log.Contains(Token));
        Assert.IsFalse(Log.Contains("private-deck"));
        Assert.IsFalse(Log.Contains("full_sync_required"));
    }

    [TestMethod]
    public async Task CancellationReachesHttpHandler()
    {
        using var Cancel = new CancellationTokenSource();
        using var Http = new HttpClient(new Handler(async (Request, Token) =>
        {
            Cancel.Cancel();
            await Task.Delay(Timeout.Infinite, Token);
            return Json("[]");
        }));
        using var Client = new AnkiClient("https://example.test/", Token, Http);
        await Assert.ThrowsAsync<OperationCanceledException>(() => Client.GetDecksAsync(Cancel.Token));
    }

    [TestMethod]
    [DataRow("http://example.com/")]
    [DataRow("ftp://localhost/")]
    [DataRow("https://user:secret@example.com/")]
    [DataRow("https://example.com/?token=x")]
    [DataRow("https://example.com/#x")]
    public void InvalidAddressesAreRejected(string Url) => Assert.ThrowsExactly<ArgumentException>(() => new AnkiClient(Url, Token));

    [TestMethod]
    [DataRow("")]
    [DataRow("bad token")]
    [DataRow("bad\r\ntoken")]
    public void InvalidTokensAreRejected(string Value) => Assert.ThrowsExactly<ArgumentException>(() => new AnkiClient("https://example.com/", Value));

    [TestMethod]
    public void InvalidIdsAndPaginationAreRejectedBeforeHttp()
    {
        using var Client = new AnkiClient("https://example.com/", Token);
        Assert.ThrowsExactly<ArgumentOutOfRangeException>(() => Client.GetNoteAsync(0));
        Assert.ThrowsExactly<ArgumentOutOfRangeException>(() => Client.GetNotesAsync(-1));
        Assert.ThrowsExactly<ArgumentOutOfRangeException>(() => Client.GetNotesAsync(Limit: 1001));
        Assert.ThrowsExactly<ArgumentOutOfRangeException>(() => Client.GetCardsAsync(NoteId: -1));
        Assert.ThrowsExactly<ArgumentOutOfRangeException>(() => Client.GetReviewsAsync(CardId: 0));
    }

    [TestMethod]
    public async Task DisposeDoesNotCloseInjectedHttpClient()
    {
        using var Http = new HttpClient(new Handler((Request, Cancel) => Task.FromResult(Json("[]"))));
        var Client = new AnkiClient("https://example.com/", Token, Http);
        Client.Dispose();
        Client.Dispose();
        await Assert.ThrowsExactlyAsync<ObjectDisposedException>(() => Client.GetDecksAsync());
        using var Response = await Http.GetAsync("https://example.com/");
        Assert.IsTrue(Response.IsSuccessStatusCode);
    }

    private static HttpResponseMessage Json(string Value) => new(HttpStatusCode.OK) { Content = new StringContent(Value, System.Text.Encoding.UTF8, "application/json") };

    private sealed class Handler(Func<HttpRequestMessage, CancellationToken, Task<HttpResponseMessage>> Respond) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage Request, CancellationToken Cancel) => Respond(Request, Cancel);
    }
}
